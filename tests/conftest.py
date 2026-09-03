"""Test harness: disposable Postgres cluster (initdb/pg_ctl) + two fresh
databases per test session. No Docker, no system Postgres, no shared state.

The cluster starts once per session; each test gets truncate-clean tables.
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


@pytest.fixture()
def env(pg_cluster, monkeypatch):
    monkeypatch.setenv("MODERN_DSN", pg_cluster["modern"])
    monkeypatch.setenv("LEGACY_DSN", pg_cluster["legacy"])
    return pg_cluster


@pytest.fixture()
def db(env):
    from app.db import Database, DbConfig
    from app.migrate import migrate

    database = Database(DbConfig.from_env())
    database.connect()
    migrate(database)  # full schema, both sides
    yield database
    _truncate_all(database)
    database.close()


@pytest.fixture()
def seeded_db(db):
    from app.migrate import SEEDS_DIR

    for seed in sorted(SEEDS_DIR.glob("*.sql")):
        for conn in (db.modern, db.legacy):
            with conn.transaction():
                conn.execute(sql.SQL(cast(LiteralString, seed.read_text())))
    return db


def _truncate_all(database) -> None:
    for conn in (database.modern, database.legacy):
        with conn.transaction():
            rows = conn.execute(
                """
                SELECT schemaname, tablename FROM pg_tables
                 WHERE schemaname IN ('modern', 'legacy', 'public')
                   AND tablename <> 'schema_migrations'
                """
            ).fetchall()
            for r in rows:
                conn.execute(
                    sql.SQL("TRUNCATE {}.{} CASCADE").format(
                        sql.Identifier(r["schemaname"]), sql.Identifier(r["tablename"])
                    )
                )


@pytest.fixture()
def engine(db):
    from app.engine import SyncEngine
    from app.mappings import MappingRegistry

    mappings = MappingRegistry.load(Path(__file__).resolve().parent.parent / "docs" / "mappings")
    return SyncEngine(db, mappings)


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient

    from app.main import app

    # Bypass lifespan (we manage the engine/DB ourselves); the app reads
    # app.state.engine for every request.
    app.state.engine = engine
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
