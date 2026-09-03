# `web/` — reconciliation console

Vite + React + TypeScript SPA. The demo UI for **legacy-sync-engine**.

## What this is

A forensic-style admin console showing the conflict-queue, audit log,
mapping inspector, and sync-trigger control. It is **part of the demo** —
not a CRUD admin, not a marketing page. The screen must make the
bi-directional-sync-with-conflict-resolution claim obvious without the README.

## Visual language

Single shared token set lives in [`src/tokens.css`](./src/tokens.css).
This file is **not** a demo-specific theme — `@frontend` is committing to
one palette / one type system reused across all three FDE demos
(legacy-sync-engine, zero-trust-tenant-rag, pii-redaction-proxy).

Aesthetic: forensic reconciliation console. Cool/neutral modern side,
warm/parchment legacy side, signal-coral for conflicts, lime for
"verified/reconciled". IBM Plex Sans + Mono + Serif (no Inter, no Roboto,
no Space Grotesk).

## How it consumes the FastAPI backend

The data layer in [`src/lib/data.ts`](./src/lib/data.ts) tries the live
`/api/*` endpoints first; on any failure it falls back to fixtures in
[`src/lib/demo-mocks.ts`](./src/lib/demo-mocks.ts) so the UI proves the
conflict-queue claim before the backend lands. A small "live · api
reachable" vs "demo · offline fixtures" pill at the bottom-left of the
sidebar exposes which mode is active.

Required endpoints (matching `docs/api_contract.md`):

| Method | Path                              | Used by                    |
|--------|-----------------------------------|----------------------------|
| GET    | `/api/health`                     | sidebar status + app-shell |
| GET    | `/api/conflicts?status=pending`   | conflict queue             |
| GET    | `/api/conflicts?status=resolved`  | resolved view              |
| POST   | `/api/conflicts/{id}/resolve`     | resolve action             |
| GET    | `/api/audit?limit=N&table=T`      | audit log                  |
| GET    | `/api/mappings`                   | mapping inspector          |
| POST   | `/api/sync/trigger`               | trigger form               |

If the FastAPI service is unavailable, the SPA continues to look and feel
correct — every conflict card is fully interactive, resolutions can be
applied, the audit log shows realistic events.

## Build & verification

```bash
# dev
npm install
npm run dev

# production build
npm run build         # tsc --noEmit && vite build → ./dist/

# visual check (start preview server, render every page with Playwright,
# assert conflict cards render, diff highlighting works, resolve action fires)
python scripts/visual_check.py
```

`dist/` is generated — never committed. The repo's `.gitignore` covers it.

## For @devops / @backend integration

The FastAPI service should mount this `web/dist/` directory as static
content at `/` (the SPA), with `/api/*` served by FastAPI itself.
`vite.config.ts` already sets `build.outDir = "dist"`, so the path in the
backend Dockerfile is `web/dist/`.

Dev proxy: in dev (`npm run dev`) the Vite server proxies `/api` to
`http://localhost:8000` via `VITE_API_TARGET`. Override with
`VITE_API_TARGET=http://other-host:port npm run dev`.

## Accessibility notes

- Every conflict card has `aria-labelledby` pointing to its heading.
- Resolution options are a `role="radiogroup"` with `role="radio"` chips.
- Live status updates use `aria-live="polite"`.
- Focus rings use the token-driven accent (not browser default).
- Sidebar pending count is announced via `aria-live`.
- All interactive elements reachable by keyboard (chips → apply button).
- Color contrast: lime `#a3b320` on `#0b0d14` is ~7.5:1; signal-coral on
  `#0b0d14` is ~5.4:1 — both pass WCAG AA for normal text.

## What is **not** in scope here

- No backend code. The FastAPI service belongs to `@backend`.
- No new mapping YAMLs. Loading is owned by the engine.
- No new tests for backend logic. Backend contract tests live in the
  backend repo. This UI has a smoke test in `scripts/visual_check.py`
  that proves the conflict-queue claim renders.