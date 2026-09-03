"""Headless visual check for the legacy-sync-engine UI.
Starts its own preview server and tears it down cleanly.
"""
import asyncio
import os
import subprocess
import sys
import time
import urllib.request
from playwright.async_api import async_playwright


WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WEB_DIR, ".visual-check")
os.makedirs(OUT, exist_ok=True)


def start_preview() -> tuple[subprocess.Popen, int]:
    env = os.environ.copy()
    # Detach from any parent process group so the preview server survives
    # between Playwright gotos (start_new_session=True is the modern
    # equivalent of preexec_fn=os.setsid and works cross-platform).
    proc = subprocess.Popen(
        ["npm", "run", "preview"],
        cwd=WEB_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        start_new_session=True,
    )
    # wait up to 30s for "Local:" — extract the port
    port = 4173
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        if "Local:" in line:
            print("preview ready:", line.strip())
            # parse port from "http://localhost:4173/"
            import re
            m = re.search(r":(\d+)/", line)
            if m:
                port = int(m.group(1))
            break
    # extra settle
    base = f"http://localhost:{port}"
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    return proc, port


def stop_preview(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()


async def capture(page, path: str, label: str, base: str) -> dict:
    url = f"{base}{path}"
    await page.goto(url, wait_until="domcontentloaded", timeout=10000)
    await page.wait_for_selector(".app-shell-inner", timeout=8000)
    await page.wait_for_timeout(500)
    png = os.path.join(OUT, f"{label}.png")
    await page.screenshot(path=png, full_page=True)
    text_head = await page.evaluate(
        "document.querySelector('main')?.innerText?.slice(0, 200) || ''"
    )
    errors = await page.evaluate(
        "Array.from(document.querySelectorAll('.error-strip')).map(e => e.innerText)"
    )
    return {"url": url, "label": label, "text": text_head.strip(), "errors": errors, "png": png}


async def run() -> int:
    preview, port = start_preview()
    base = f"http://localhost:{port}"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await context.new_page()

            console_errors: list[str] = []
            page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

            results = []
            for path, label in [
                ("/", "root-redirect"),
                ("/conflicts", "01-conflicts-pending"),
                ("/conflicts/resolved", "02-conflicts-resolved"),
                ("/audit", "03-audit"),
                ("/mappings", "04-mappings"),
                ("/trigger", "05-trigger"),
            ]:
                r = await capture(page, path, label, base)
                results.append(r)
                print(f"OK {label}: {r['text'][:80]!r}")

            # Assertions on conflict page
            await page.goto(f"{base}/conflicts", wait_until="domcontentloaded")
            await page.wait_for_selector(".conflict-card", timeout=12000)
            await page.wait_for_timeout(1200)
            card_count = await page.locator(".conflict-card").count()
            diff_rows = await page.locator(".field-row.diff").count()
            side_modern = await page.locator(".side.modern").count()
            side_legacy = await page.locator(".side.legacy").count()
            chips = await page.locator(".resolution-chip").count()

            # A11y checks
            card_label_count = await page.locator(".conflict-card[aria-labelledby]").count()
            pending_count = await page.locator(".nav-link.active .count").count()

            print()
            print(f"conflict cards: {card_count}")
            print(f"diff-highlighted fields: {diff_rows}")
            print(f"modern panels: {side_modern} / legacy panels: {side_legacy}")
            print(f"resolution chips: {chips}")
            print(f"cards with aria-labelledby: {card_label_count}")
            print(f"sidebar pending badge: {pending_count}")
            print(f"console errors: {console_errors}")

            assert card_count >= 3, f"expected >=3 cards, got {card_count}"
            assert diff_rows > 0, "no diff highlighting"
            assert side_modern == side_legacy == card_count
            assert chips >= card_count * 2, "each conflict should have >=2 resolution chips"
            assert card_label_count == card_count, "a11y: each conflict card should have aria-labelledby"

            # Test the resolve interaction
            first_card = page.locator(".conflict-card").first
            legacy_chip = first_card.locator(".resolution-chip").nth(1)
            await legacy_chip.click()
            apply_btn = first_card.locator(".btn-primary").first
            await apply_btn.click()
            await page.wait_for_timeout(1500)
            new_card_count = await page.locator(".conflict-card").count()
            print(f"cards after resolve: {new_card_count} (was {card_count})")
            assert new_card_count < card_count, "resolve should remove a conflict from pending queue"

            await browser.close()
            return 0
    finally:
        stop_preview(preview)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))