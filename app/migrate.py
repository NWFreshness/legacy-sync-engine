"""Migration runner — applies db/migrations/*.sql in order to BOTH
databases. Statements tagged with `-- MODERN_BEGIN/END` go only to the
modern_saas connection; `-- LEGACY_BEGIN/END` only to legacy_erp; everything
else (including `-- BOTH_BEGIN/END`) goes to both.

Records applied versions in schema_migrations (one row per DB).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import LiteralString, cast

import psycopg
from psycopg import sql

from .db import Database

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
SEEDS_DIR = Path(__file__).resolve().parent.parent / "db" / "seeds"

_SECTION = re.compile(r"-- (MODERN|LEGACY|BOTH)_BEGIN|(--) (MODERN|LEGACY|BOTH)_END")


def _split(text: str) -> dict[str, list[str]]:
    """Return {modern: [...], legacy: [...], both: [...]} chunks of SQL."""
    sections: dict[str, list[str]] = {"MODERN": [], "LEGACY": [], "BOTH": []}
    current = "BOTH"  # anything outside a tag is shared
    buf: list[str] = []
    for line in text.splitlines(keepends=True):
        m = _SECTION.match(line.strip())
        if m:
            if buf:
                sections[current].append("".join(buf))
                buf = []
            if m.group(1):
                current = m.group(1)
            else:
                current = "BOTH"
        else:
            buf.append(line)
    if buf:
        sections[current].append("".join(buf))
    return sections


def _applied(conn) -> set[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    return {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def _exec_chunks(conn, chunks: list[str]) -> None:
    for chunk in chunks:
        s = chunk.strip()
        if not s:
            continue
        conn.execute(sql.SQL(cast(LiteralString, s)))

def migrate(db: Database, *, seed: bool = False) -> list[str]:
    applied: list[str] = []
    files = sorted(
        p for p in MIGRATIONS_DIR.glob("*.sql") if not p.name.endswith(".down.sql")
    )
    for name, conn in (("modern", db.modern), ("legacy", db.legacy)):
        with conn.transaction():
            done = _applied(conn)
            for path in files:
                version = path.name
                if version in done:
                    continue
                parts = _split(path.read_text())
                if name == "modern":
                    _exec_chunks(conn, parts["MODERN"] + parts["BOTH"])
                else:
                    _exec_chunks(conn, parts["LEGACY"] + parts["BOTH"])
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                )
                applied.append(f"{name}:{version}")
            if seed:
                for seed_path in sorted(SEEDS_DIR.glob("*.sql")):
                    parts = _split(seed_path.read_text())
                    if name == "modern":
                        _exec_chunks(conn, parts["MODERN"] + parts["BOTH"])
                    else:
                        _exec_chunks(conn, parts["LEGACY"] + parts["BOTH"])
                    applied.append(f"{name}:seed")
    return applied


def downgrade(db: Database, version: str) -> None:
    """Roll back one migration on both databases (demo/dev use)."""
    down = MIGRATIONS_DIR / version.replace(".sql", ".down.sql")
    if not down.exists():
        raise FileNotFoundError(f"no down migration for {version}")
    for conn in (db.modern, db.legacy):
        with conn.transaction():
            conn.execute(sql.SQL(cast(LiteralString, down.read_text())))
            conn.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))


def main() -> None:  # python -m app.migrate [--seed] [--down 001_init.sql]
    import argparse

    parser = argparse.ArgumentParser(description="Apply DB migrations")
    parser.add_argument("--seed", action="store_true", help="load db/seeds after migrating")
    parser.add_argument("--down", metavar="VERSION", help="roll back one migration")
    args = parser.parse_args()

    from .db import DbConfig

    db = Database(DbConfig.from_env())
    db.connect()
    try:
        if args.down:
            downgrade(db, args.down)
            print(f"rolled back {args.down}")
        else:
            applied = migrate(db, seed=args.seed)
            for line in applied:
                print(f"applied {line}")
            if not applied:
                print("migrations up to date")
    finally:
        db.close()


if __name__ == "__main__":
    main()
