"""Database access — two psycopg connections, no ORM.

The legacy path intentionally uses raw SQL against ugly column names
(CUST_MSTR, ORD_HDR, INV_BAL) to stay faithful to the "20-year-old ERP"
simulation. Sync infrastructure (audit_log, conflicts, dead_letter_queue,
sync_state) lives in the *modern* database's public schema, matching
docs/data_model.sql.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DictConn = psycopg.Connection[dict[str, Any]]


class AuditJsonEncoder(json.JSONEncoder):
    """JSONB payloads carry datetimes/Decimals/UUIDs from DB rows; serialize
    deterministically instead of crashing the audit write."""

    def default(self, o: Any) -> Any:
        if isinstance(o, (dt.datetime, dt.date, dt.time)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, bytes):
            return o.hex()
        return super().default(o)


@dataclass(frozen=True)
class DbConfig:
    modern_dsn: str
    legacy_dsn: str

    @classmethod
    def from_env(cls) -> "DbConfig":
        return cls(
            modern_dsn=os.environ.get(
                "MODERN_DSN",
                "postgresql://postgres:postgres@localhost:5432/modern_saas",
            ),
            legacy_dsn=os.environ.get(
                "LEGACY_DSN",
                "postgresql://postgres:postgres@localhost:5433/legacy_erp",
            ),
        )


class Database:
    """Holds one connection per side. Not thread-safe; the app creates one
    per process and serializes access through the sync engine's lock."""

    def __init__(self, config: DbConfig):
        self.config = config
        self._modern: DictConn | None = None
        self._legacy: DictConn | None = None

    def connect(self) -> None:
        # psycopg's connect() overloads lose the row_factory type; cast.
        self._modern = cast(
            DictConn, psycopg.connect(self.config.modern_dsn, row_factory=dict_row)
        )
        self._legacy = cast(
            DictConn, psycopg.connect(self.config.legacy_dsn, row_factory=dict_row)
        )

    def close(self) -> None:
        for conn in (self._modern, self._legacy):
            if conn is not None and not conn.closed:
                conn.close()
        self._modern = self._legacy = None

    @property
    def modern(self) -> DictConn:
        assert self._modern is not None and not self._modern.closed, "DB not connected"
        return self._modern

    @property
    def legacy(self) -> DictConn:
        assert self._legacy is not None and not self._legacy.closed, "DB not connected"
        return self._legacy

    @contextmanager
    def modern_tx(self) -> Iterator[DictConn]:
        with self.modern.transaction():
            yield self.modern

    @contextmanager
    def legacy_tx(self) -> Iterator[DictConn]:
        with self.legacy.transaction():
            yield self.legacy

    def ping(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, conn in (("modern_db", self._modern), ("legacy_db", self._legacy)):
            try:
                if conn is None or conn.closed:
                    raise psycopg.OperationalError("not connected")
                conn.execute("SELECT 1")
                out[name] = "up"
            except Exception:
                out[name] = "down"
        return out


def j(value: Any) -> Jsonb:
    """Wrap a dict for JSONB insert (audit/conflict/DLQ payloads)."""
    return Jsonb(value, dumps=lambda v: json.dumps(v, cls=AuditJsonEncoder))
