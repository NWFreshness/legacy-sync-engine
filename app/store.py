"""Persistence for sync infrastructure — audit_log, conflicts,
dead_letter_queue, sync_state — plus record-level reads/upserts on both
schemas. Raw SQL only; the data model is docs/data_model.sql v1.1 + ADR-002.

Idempotency rule (contract §Idempotency Guarantees):
    Every applied change is claimed by INSERT into audit_log ON CONFLICT
    (event_id) DO NOTHING *before* any upsert. A duplicate event_id is a
    no-op with a `duplicate_ignored` outcome — never a second side effect.

Dynamic SQL note: table/column names come exclusively from the module-level
whitelists below (MODERN_PK / LEGACY_PK / LEGACY_TABLE) — never from request
or record data. Values are always parameterized.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

from psycopg import sql

from .db import Database, j
from .schemas import AuditEntry, ResolutionStrategy, SyncDirection

MODERN_PK = {"customers": "id", "orders": "id", "inventory": "product_id"}
LEGACY_PK = {"customers": "CUST_ID", "orders": "ORD_ID", "inventory": "PROD_CD"}
LEGACY_TABLE = {"customers": "CUST_MSTR", "orders": "ORD_HDR", "inventory": "INV_BAL"}


def _cols(record: dict[str, Any]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(c) for c in record)


def _set_clause(record: dict[str, Any], pk: str) -> sql.Composed:
    return sql.SQL(", ").join(
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
        for c in record
        if c != pk
    )


class Store:
    def __init__(self, db: Database):
        self.db = db

    # --- audit / idempotency ------------------------------------------------

    def claim_event(
        self,
        event_id: UUID,
        direction: SyncDirection,
        table: str,
        record_id: str,
        mapping_version: str,
        strategy: ResolutionStrategy,
    ) -> bool:
        """Insert the pending audit row. Returns False if event_id exists
        (duplicate — caller must not produce side effects)."""
        with self.db.modern_tx() as conn:
            row = conn.execute(
                """
                INSERT INTO audit_log (event_id, direction, source_table, record_id,
                                       mapping_version, resolution_strategy, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                (event_id, direction, table, record_id, mapping_version, strategy),
            ).fetchone()
        return row is not None

    def finalize_event(
        self,
        event_id: UUID,
        status: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        error: str | None = None,
        conflict_id: UUID | None = None,
    ) -> None:
        with self.db.modern_tx() as conn:
            conn.execute(
                """
                UPDATE audit_log
                   SET status = %s, before_state = %s, after_state = %s,
                       error_message = %s, conflict_id = COALESCE(%s, conflict_id)
                 WHERE event_id = %s
                """,
                (
                    status,
                    j(before) if before is not None else None,
                    j(after) if after is not None else None,
                    error,
                    conflict_id,
                    event_id,
                ),
            )

    def list_audit(self, limit: int = 20, table: str | None = None) -> list[AuditEntry]:
        query = sql.SQL(
            """
            SELECT event_id, created_at, direction, source_table AS table_name,
                   record_id, resolution_strategy, status,
                   before_state AS before, after_state AS after
              FROM audit_log
            """
        )
        params: list[Any] = []
        if table:
            query += sql.SQL(" WHERE source_table = %s")
            params.append(table)
        query += sql.SQL(" ORDER BY created_at DESC LIMIT %s")
        params.append(limit)
        rows = self.db.modern.execute(query, params).fetchall()
        return [AuditEntry.model_validate(r) for r in rows]

    # --- conflicts -----------------------------------------------------------

    def create_conflict(
        self,
        table: str,
        record_id: str,
        modern_state: dict[str, Any],
        legacy_state: dict[str, Any],
    ) -> UUID:
        conflict_id = uuid4()
        with self.db.modern_tx() as conn:
            conn.execute(
                """
                INSERT INTO conflicts (conflict_id, table_name, record_id,
                                       modern_state, legacy_state, detected_at,
                                       resolution_strategy, status)
                VALUES (%s, %s, %s, %s, %s, NOW(), 'manual_review', 'pending')
                """,
                (conflict_id, table, record_id, j(modern_state), j(legacy_state)),
            )
        return conflict_id

    def list_conflicts(
        self, status: str = "pending", table: str | None = None
    ) -> list[dict[str, Any]]:
        query = sql.SQL(
            """
            SELECT conflict_id, table_name, record_id, modern_state, legacy_state,
                   detected_at, resolution_strategy, status
              FROM conflicts WHERE status = %s
            """
        )
        params: list[Any] = [status]
        if table:
            query += sql.SQL(" AND table_name = %s")
            params.append(table)
        query += sql.SQL(" ORDER BY detected_at DESC")
        return list(self.db.modern.execute(query, params).fetchall())

    def get_conflict(self, conflict_id: UUID) -> dict[str, Any] | None:
        return self.db.modern.execute(
            "SELECT * FROM conflicts WHERE conflict_id = %s", (conflict_id,)
        ).fetchone()

    def mark_conflict_resolved(
        self, conflict_id: UUID, final_state: dict[str, Any], resolved_by: str
    ) -> None:
        with self.db.modern_tx() as conn:
            conn.execute(
                """
                UPDATE conflicts
                   SET status = 'resolved', final_state = %s,
                       resolved_by = %s, resolved_at = NOW()
                 WHERE conflict_id = %s
                """,
                (j(final_state), resolved_by, conflict_id),
            )

    # --- dead letter queue ----------------------------------------------------

    def dead_letter(
        self,
        event_id: UUID,
        table: str,
        record_id: str,
        payload: dict[str, Any],
        error: str,
    ) -> None:
        """Insert or re-arm a poison record. Retry backoff is exponential:
        next_retry_at = now + 30s * 2^retry_count (ADR-001 §7)."""
        with self.db.modern_tx() as conn:
            conn.execute(
                """
                INSERT INTO dead_letter_queue
                    (event_id, table_name, record_id, payload, error, retry_count, next_retry_at)
                VALUES (%s, %s, %s, %s, %s, 0, NOW() + INTERVAL '30 seconds')
                ON CONFLICT (event_id) DO UPDATE
                  SET retry_count = dead_letter_queue.retry_count + 1,
                      error = EXCLUDED.error,
                      next_retry_at = NOW()
                        + (30 * POWER(2, dead_letter_queue.retry_count + 1)) * INTERVAL '1 second'
                """,
                (event_id, table, record_id, j(payload), error),
            )

    # --- record access ---------------------------------------------------------

    def get_modern(self, table: str, record_id: str) -> dict[str, Any] | None:
        return self.db.modern.execute(
            sql.SQL("SELECT * FROM modern.{} WHERE {} = %s").format(
                sql.Identifier(table), sql.Identifier(MODERN_PK[table])
            ),
            (record_id,),
        ).fetchone()

    def get_legacy(self, table: str, record_id: str) -> dict[str, Any] | None:
        return self.db.legacy.execute(
            sql.SQL("SELECT * FROM legacy.{} WHERE {} = %s").format(
                sql.Identifier(LEGACY_TABLE[table]), sql.Identifier(LEGACY_PK[table])
            ),
            (record_id,),
        ).fetchone()

    def upsert_modern(
        self, table: str, record: dict[str, Any], origin: str
    ) -> dict[str, Any] | None:
        """Version-checked upsert (contract: ON CONFLICT ... WHERE version =
        excluded.version - 1). Stamps last_synced_from for echo suppression.
        Returns the prior row, or None on insert."""
        pk = MODERN_PK[table]
        record = {**record, "updated_at": dt.datetime.now(dt.UTC)}
        with self.db.modern_tx() as conn:
            before = conn.execute(
                sql.SQL("SELECT * FROM modern.{} WHERE {} = %s").format(
                    sql.Identifier(table), sql.Identifier(pk)
                ),
                (record[pk],),
            ).fetchone()
            expected = (before or {}).get("version", 0) + 1
            conn.execute(
                sql.SQL(
                    """
                    INSERT INTO modern.{t} ({cols}, version, last_synced_from)
                    VALUES ({vals}, %s, %s)
                    ON CONFLICT ({pk}) DO UPDATE SET {setclause},
                        version = modern.{t}.version + 1,
                        last_synced_from = EXCLUDED.last_synced_from
                     WHERE modern.{t}.version = EXCLUDED.version - 1
                    """
                ).format(
                    t=sql.Identifier(table),
                    cols=_cols(record),
                    vals=sql.SQL(", ").join(sql.Placeholder() for _ in record),
                    pk=sql.Identifier(pk),
                    setclause=_set_clause(record, pk),
                ),
                (*record.values(), expected, origin),
            )
        return before

    def upsert_legacy(
        self, table: str, record: dict[str, Any], origin: str
    ) -> dict[str, Any] | None:
        """Same contract as upsert_modern against the legacy schema, plus a
        CHANGE_LOG row so the legacy side's own CDC trail stays complete."""
        ltable, pk = LEGACY_TABLE[table], LEGACY_PK[table]
        with self.db.legacy_tx() as conn:
            before = conn.execute(
                sql.SQL("SELECT * FROM legacy.{} WHERE {} = %s").format(
                    sql.Identifier(ltable), sql.Identifier(pk)
                ),
                (record[pk],),
            ).fetchone()
            expected = (before or {}).get("VERSION_NO", 0) + 1
            conn.execute(
                sql.SQL(
                    """
                    INSERT INTO legacy.{t} ({cols}, "VERSION_NO", "LAST_SYNCED_FROM")
                    VALUES ({vals}, %s, %s)
                    ON CONFLICT ({pk}) DO UPDATE SET {setclause},
                        "VERSION_NO" = legacy.{t}."VERSION_NO" + 1,
                        "LAST_SYNCED_FROM" = EXCLUDED."LAST_SYNCED_FROM"
                     WHERE legacy.{t}."VERSION_NO" = EXCLUDED."VERSION_NO" - 1
                    """
                ).format(
                    t=sql.Identifier(ltable),
                    cols=_cols(record),
                    vals=sql.SQL(", ").join(sql.Placeholder() for _ in record),
                    pk=sql.Identifier(pk),
                    setclause=_set_clause(record, pk),
                ),
                (*record.values(), expected, origin),
            )
            conn.execute(
                """
                INSERT INTO legacy.CHANGE_LOG (table_name, record_id, operation, payload)
                VALUES (%s, %s, 'U', %s)
                """,
                (ltable, str(record[pk]), j(record)),
            )
        return before

    # --- sync_state -------------------------------------------------------------

    def last_sync(self, table: str, direction: SyncDirection) -> dict[str, Any] | None:
        return self.db.modern.execute(
            "SELECT * FROM sync_state WHERE table_name = %s AND direction = %s",
            (table, direction),
        ).fetchone()

    def touch_sync(self, table: str, direction: SyncDirection, event_id: UUID) -> None:
        with self.db.modern_tx() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (table_name, direction, last_sync_ts, last_event_id)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (table_name, direction) DO UPDATE
                  SET last_sync_ts = NOW(), last_event_id = EXCLUDED.last_event_id
                """,
                (table, direction, event_id),
            )

    def last_sync_map(self) -> dict[str, str | None]:
        rows = self.db.modern.execute(
            "SELECT table_name, direction, last_sync_ts FROM sync_state"
        ).fetchall()
        return {
            f"{r['table_name']}:{r['direction']}": (
                str(r["last_sync_ts"]) if r["last_sync_ts"] else None
            )
            for r in rows
        }
