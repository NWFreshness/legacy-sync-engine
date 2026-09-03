"""Test harness: disposable Postgres cluster (initdb/pg_ctl) + one shared
database pair per session. Each test resets schemas via TRUNCATE before
running, so tests are isolated without per-test database creation overhead.

Migrations run through app.migrate.migrate() so tests exercise the exact
files compose will run (db/migrations/001_init.sql).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import LiteralString, cast

import psycopg
import pytest
from psycopg import sql

PGBIN = Path("/usr/lib/postgresql/16/bin")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


@pytest.fixture(scope="session")
def pg_cluster():
    # Clean up clusters orphaned by killed runs (same prefix, dead pid).
    for stale in Path(tempfile.gettempdir()).glob("lse-pg-*"):
        pidfile = stale / "postmaster.pid"
        try:
            pid = int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
            if pid:
                os.kill(pid, 0)  # alive -> not ours to touch
                continue
        except (ProcessLookupError, ValueError, IndexError):
            pass
        except PermissionError:
            continue
        shutil.rmtree(stale, ignore_errors=True)

    port = _free_port()
    datadir = Path(tempfile.mkdtemp(prefix="lse-pg-"))
    socketdir = Path(tempfile.mkdtemp(prefix="lse-sock-"))
    _run([str(PGBIN / "initdb"), "-D", str(datadir), "-U", "postgres", "--auth=trust"])
    _run(
        [
            str(PGBIN / "pg_ctl"), "-D", str(datadir), "-l", str(datadir / "log"),
            "-o", f"-p {port} -k {socketdir} -c listen_addresses=127.0.0.1",
            "-w", "start",
        ]
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            psycopg.connect(f"host={socketdir} port={port} user=postgres dbname=postgres").close()
            break
        except psycopg.OperationalError:
            time.sleep(0.2)
    else:
        raise RuntimeError("postgres did not start; see " + str(datadir / "log"))

    admin = f"host={socketdir} port={port} user=postgres dbname=postgres"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("CREATE DATABASE modern_saas")
        conn.execute("CREATE DATABASE legacy_erp")

    yield {
        "modern": f"host={socketdir} port={port} user=postgres dbname=modern_saas",
        "legacy": f"host={socketdir} port={port} user=postgres dbname=legacy_erp",
    }

    _run([str(PGBIN / "pg_ctl"), "-D", str(datadir), "-m", "fast", "-w", "stop"])


# Set DSN env vars at import time so the session-scoped `db` fixture (and any
# module that reads DSNs at import time) sees the right values regardless of
# pytest fixture resolution order.
@pytest.fixture(scope="session", autouse=True)
def _set_test_env(pg_cluster):
    os.environ["MODERN_DSN"] = pg_cluster["modern"]
    os.environ["LEGACY_DSN"] = pg_cluster["legacy"]
    yield
    os.environ.pop("MODERN_DSN", None)
    os.environ.pop("LEGACY_DSN", None)


@pytest.fixture()
def db(pg_cluster):
    from app.db import Database, DbConfig

    database = Database(DbConfig.from_env())
    database.connect()
    yield database
    database.close()


def _truncate_all(database) -> None:
    for conn in (database.modern, database.legacy):
        with conn.transaction():
            rows = conn.execute(
                """
                SELECT schemaname, tablename FROM pg_tables
                 WHERE schemaname IN ('modern', 'legacy', 'public')
                """
            ).fetchall()
            for r in rows:
                conn.execute(
                    sql.SQL("TRUNCATE {}.{} CASCADE").format(
                        sql.Identifier(r["schemaname"]), sql.Identifier(r["tablename"])
                    )
                )


@pytest.fixture()
def clean_db(db):
    """Truncate all application tables before the test runs."""
    _truncate_all(db)
    yield db


@pytest.fixture()
def migrated_db(clean_db):
    from app.migrate import migrate

    migrate(clean_db)
    yield clean_db


@pytest.fixture()
def seeded_db(migrated_db):
    from app.migrate import _split

    for seed_path in sorted((Path(__file__).resolve().parent.parent / "db" / "seeds").glob("*.sql")):
        parts = _split(seed_path.read_text())
        for name, conn in (("modern", migrated_db.modern), ("legacy", migrated_db.legacy)):
            chunks = parts["MODERN"] + parts["BOTH"] if name == "modern" else parts["LEGACY"] + parts["BOTH"]
            with conn.transaction():
                for chunk in chunks:
                    s = chunk.strip()
                    if s:
                        conn.execute(sql.SQL(cast(LiteralString, s)))
    yield migrated_db


@pytest.fixture()
def engine(migrated_db):
    from app.engine import SyncEngine
    from app.mappings import MappingRegistry

    mappings = MappingRegistry.load(Path(__file__).resolve().parent.parent / "docs" / "mappings")
    yield SyncEngine(migrated_db, mappings)


@pytest.fixture()
def seeded_engine(seeded_db):
    from app.engine import SyncEngine
    from app.mappings import MappingRegistry

    mappings = MappingRegistry.load(Path(__file__).resolve().parent.parent / "docs" / "mappings")
    yield SyncEngine(seeded_db, mappings)


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient

    from app.main import app

    # Do not run the app's lifespan (we own the engine/DB). TestClient
    # without a context manager skips lifespan events entirely.
    app.state.engine = engine
    c = TestClient(app, raise_server_exceptions=True)
    yield c
