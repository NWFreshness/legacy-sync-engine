"""Sync engine — the core of the demo.

Reads changes from either side (poller or webhook), maps fields through the
versioned YAML layer, resolves conflicts per the table's configured
strategy, and writes an audit row for every event.

Conflict semantics (ADR-002 approved reading 2):
  - last_write_wins: compare updated_at; |Δ| <= clock_skew_tolerance_seconds
    means "simultaneous" and the mapping's source_of_truth side wins
    (deterministic tie-break). Outside tolerance, the newer row wins.
  - source_of_truth_wins: the designated side always wins; the other side's
    change is recorded as superseded.
  - manual_review: any change where both sides already hold the record is
    queued; nothing is auto-applied. First-time propagation applies.

Echo suppression (ADR-002): every sync-applied write stamps
last_synced_from; the poller skips rows whose stamp matches the direction's
origin, which breaks bidirectional loops.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import sql

from .db import Database
from .mappings import MappingError, MappingRegistry, apply_mapping
from .schemas import (
    AuditEntry,
    SyncDirection,
    TableMapping,
    WebhookPayload,
)
from .store import LEGACY_PK, LEGACY_TABLE, MODERN_PK, Store

Origin = Literal["sync:modern", "sync:legacy", "admin", "seed"]

DIRECTION_ORIGIN: dict[SyncDirection, Origin] = {
    "modern_to_legacy": "sync:modern",
    "legacy_to_modern": "sync:legacy",
}


@dataclass
class SyncOutcome:
    event_id: UUID
    status: str  # applied | conflict_queued | duplicate_ignored | dead_lettered | no_change
    detail: str = ""
    conflict_id: UUID | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class SyncEngine:
    def __init__(self, db: Database, mappings: MappingRegistry):
        self.db = db
        self.mappings = mappings
        self.store = Store(db)
        self._lock = threading.Lock()  # serialize engine mutations per process

    # ------------------------------------------------------------------ public

    def handle_webhook(self, payload: WebhookPayload) -> SyncOutcome:
        """Real-time path. Idempotent on event_id (contract §webhooks)."""
        direction: SyncDirection = (
            "modern_to_legacy" if payload.source == "modern" else "legacy_to_modern"
        )
        mapping = self.mappings.get(payload.table)
        # Source-side PK is the raw field from the incoming record.
        src_pk_field = MODERN_PK[mapping.table] if payload.source == "modern" else LEGACY_PK[mapping.table]
        record_id = str(payload.record.get(src_pk_field) or uuid4())
        with self._lock:
            if not self.store.claim_event(
                payload.event_id, direction, payload.table, record_id,
                mapping.version, mapping.conflict_strategy,
            ):
                return SyncOutcome(payload.event_id, "duplicate_ignored")
            return self._apply(direction, mapping, record_id, payload.event_id,
                               incoming=payload.record, incoming_ts=payload.timestamp)

    def poll_once(
        self, table: str, direction: SyncDirection | Literal["both"] = "both"
    ) -> list[SyncOutcome]:
        """CDC-style poller. Modern side: updated_at > watermark. Legacy
        side: CHANGE_LOG rows newer than the watermark (tables without
        timestamps are only observable this way)."""
        outcomes: list[SyncOutcome] = []
        directions: list[SyncDirection] = (
            ["modern_to_legacy", "legacy_to_modern"] if direction == "both" else [direction]
        )
        for d in directions:
            outcomes.extend(self._poll_direction(table, d))
        return outcomes

    def resolve_conflict(
        self, conflict_id: UUID, chosen_state: dict[str, Any], resolved_by: str
    ) -> SyncOutcome:
        """Admin resolution: write chosen_state (modern-canonical) to BOTH
        sides, audit it, close the conflict."""
        conflict = self.store.get_conflict(conflict_id)
        if conflict is None:
            raise KeyError(f"conflict {conflict_id} not found")
        if conflict["status"] != "pending":
            raise ValueError(f"conflict {conflict_id} already {conflict['status']}")

        table = conflict["table_name"]
        record_id = conflict["record_id"]
        mapping = self.mappings.get(table)
        event_id = uuid4()
        with self._lock:
            self.store.claim_event(event_id, "modern_to_legacy", table, record_id,
                                   mapping.version, "manual_review")
            before = {
                "modern": self.store.get_modern(table, record_id),
                "legacy": self.store.get_legacy(table, record_id),
            }
            self._write_both(mapping, record_id, chosen_state, origin="admin")
            self.store.finalize_event(
                event_id, "applied", before=before,
                after={"resolved": chosen_state, "resolved_by": resolved_by},
                conflict_id=conflict_id,
            )
            self.store.mark_conflict_resolved(conflict_id, chosen_state, resolved_by)
        return SyncOutcome(event_id, "applied", conflict_id=conflict_id)

    def list_audit(self, limit: int = 20, table: str | None = None) -> list[AuditEntry]:
        return self.store.list_audit(limit, table)

    def health(self) -> dict[str, Any]:
        return {
            **self.db.ping(),
            "last_sync": self.store.last_sync_map() if self.db.ping()["modern_db"] == "up" else {},
            "mapping_versions": sorted({m.version for m in self.mappings.summaries()}),
        }

    # ----------------------------------------------------------------- engine

    def _poll_direction(self, table: str, direction: SyncDirection) -> list[SyncOutcome]:
        mapping = self.mappings.get(table)
        state = self.store.last_sync(table, direction)
        since: dt.datetime = (state or {}).get("last_sync_ts") or dt.datetime.min.replace(
            tzinfo=dt.UTC
        )
        outcomes: list[SyncOutcome] = []
        last_event_id: UUID | None = None
        if direction == "modern_to_legacy":
            rows = self.db.modern.execute(
                sql.SQL(
                    "SELECT * FROM modern.{} WHERE updated_at > %s ORDER BY updated_at"
                ).format(sql.Identifier(table)),
                (since,),
            ).fetchall()
            for row in rows:
                if (row.get("last_synced_from") or "") == DIRECTION_ORIGIN["legacy_to_modern"]:
                    continue  # echo suppression: we wrote this from the far side
                rid = str(row[MODERN_PK[table]])
                event_id = uuid4()
                with self._lock:
                    if self.store.claim_event(event_id, direction, table, rid,
                                              mapping.version, mapping.conflict_strategy):
                        outcomes.append(self._apply(direction, mapping, rid, event_id,
                                                    incoming=dict(row),
                                                    incoming_ts=row.get("updated_at")))
                        last_event_id = event_id
            if last_event_id is not None:
                self.store.touch_sync(table, direction, last_event_id)
        else:
            rows = self.db.legacy.execute(
                sql.SQL(
                    'SELECT * FROM legacy."CHANGE_LOG" WHERE table_name = %s AND changed_at > %s ORDER BY changed_at'
                ),
                (LEGACY_TABLE[table], since),
            ).fetchall()
            for row in rows:
                rid = str(row["record_id"])
                current = self.store.get_legacy(table, rid)
                if current is None:
                    continue
                if (current.get("LAST_SYNCED_FROM") or "") == DIRECTION_ORIGIN["modern_to_legacy"]:
                    continue  # echo suppression
                event_id = uuid4()
                with self._lock:
                    if self.store.claim_event(event_id, direction, table, rid,
                                              mapping.version, mapping.conflict_strategy):
                        outcomes.append(self._apply(direction, mapping, rid, event_id,
                                                    incoming=dict(current),
                                                    incoming_ts=row.get("changed_at")))
                        last_event_id = event_id
            if last_event_id is not None:
                self.store.touch_sync(table, direction, last_event_id)
        return outcomes

    def _apply(
        self,
        direction: SyncDirection,
        mapping: TableMapping,
        record_id: str,
        event_id: UUID,
        incoming: dict[str, Any],
        incoming_ts: dt.datetime | None,
    ) -> SyncOutcome:
        """Map + conflict-check + upsert one record. Caller holds the lock
        and has claimed the event_id."""
        table = mapping.table
        source: Literal["modern", "legacy"] = (
            "modern" if direction == "modern_to_legacy" else "legacy"
        )
        # The "record_id" we got from the caller is the *source-side* PK.
        # For lookups on the far side, we need the far-side PK, which the
        # mapping derives via transforms (e.g. id -> CUST_ID truncation).
        far_id = self._far_side_pk(mapping, incoming, direction)
        try:
            if direction == "modern_to_legacy":
                modern_row = self.store.get_modern(table, record_id)
                legacy_row = self.store.get_legacy(table, far_id) if far_id else None
            else:
                legacy_row = self.store.get_legacy(table, record_id)
                # Validate far_id is a usable PK for the far-side column type.
                # If the mapping produced something the far-side column can't
                # accept (e.g. a non-UUID for a UUID column), treat as new.
                modern_row = self._safe_get_modern(table, far_id)
            decision = self._decide(mapping, modern_row, legacy_row, source, incoming_ts)

            if decision == "conflict":
                conflict_id = self._queue_conflict(mapping, record_id, modern_row, legacy_row)
                self.store.finalize_event(
                    event_id, "conflicted",
                    before={"modern": modern_row, "legacy": legacy_row},
                    after=None, error="conflict queued for manual review",
                    conflict_id=conflict_id,
                )
                return SyncOutcome(event_id, "conflict_queued", conflict_id=conflict_id)

            if decision == "skip":  # lost the race; audit as superseded
                self.store.finalize_event(
                    event_id, "applied",
                    before={"modern": modern_row, "legacy": legacy_row},
                    after=None, error="superseded by conflict policy",
                )
                return SyncOutcome(event_id, "no_change", "superseded by conflict policy")

            mapped = apply_mapping(mapping, incoming, direction)
            origin = DIRECTION_ORIGIN[direction]
            if direction == "modern_to_legacy":
                # mapped already carries the legacy PK (from the id transform).
                before_far = self.store.upsert_legacy(table, mapped, origin)
            else:
                before_far = self.store.upsert_modern(table, mapped, origin)
            self.store.finalize_event(
                event_id, "applied",
                before={"modern": modern_row, "legacy": legacy_row},
                after=mapped,
            )
            self.store.touch_sync(table, direction, event_id)
            return SyncOutcome(event_id, "applied")
        except Exception as exc:  # poison pill -> DLQ (MappingError included)
            self.store.finalize_event(event_id, "dead_lettered",
                                      before=None, after=None, error=str(exc))
            self.store.dead_letter(event_id, table, record_id, incoming, str(exc))
            return SyncOutcome(event_id, "dead_lettered", str(exc))

    def _decide(
        self,
        mapping: TableMapping,
        modern_row: dict[str, Any] | None,
        legacy_row: dict[str, Any] | None,
        source: Literal["modern", "legacy"],
        incoming_ts: dt.datetime | None,
    ) -> Literal["apply", "conflict", "skip"]:
        strategy = mapping.conflict_strategy
        both_exist = modern_row is not None and legacy_row is not None

        if strategy == "manual_review":
            return "conflict" if both_exist else "apply"

        if strategy == "source_of_truth_wins":
            truth = mapping.source_of_truth
            if truth == "both":
                return "conflict" if both_exist else "apply"
            return "apply" if source == truth else "skip"

        # last_write_wins
        if not both_exist:
            return "apply"
        other = legacy_row if source == "modern" else modern_row
        assert other is not None  # both_exist guarantees this
        other_ts = self._row_ts(other, "legacy" if source == "modern" else "modern")
        if incoming_ts is None or other_ts is None:
            # Legacy rows without a comparable timestamp cannot win a race;
            # let the change apply (ADR-001 consequence: poller-based).
            return "apply"
        tol = dt.timedelta(seconds=mapping.clock_skew_tolerance_seconds)
        delta = abs(self._aware(incoming_ts) - self._aware(other_ts))
        if delta <= tol:
            truth = mapping.source_of_truth
            if truth == "both":
                return "conflict"  # simultaneous + no designated winner
            return "apply" if source == truth else "skip"
        return "apply" if self._aware(incoming_ts) > self._aware(other_ts) else "skip"

    # ----------------------------------------------------------------- helpers

    def _queue_conflict(
        self,
        mapping: TableMapping,
        record_id: str,
        modern_row: dict[str, Any] | None,
        legacy_row: dict[str, Any] | None,
    ) -> UUID:
        return self.store.create_conflict(
            mapping.table, record_id, modern_row or {}, legacy_row or {}
        )

    def proposed_resolutions(
        self, mapping: TableMapping, modern_row: dict[str, Any], legacy_row: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Modern-canonical options for the UI (ADR-002 reading 4)."""
        try:
            legacy_as_modern = apply_mapping(mapping, legacy_row, "legacy_to_modern")
        except MappingError:
            legacy_as_modern = legacy_row
        return [
            {"name": "modern", "state": modern_row},
            {"name": "legacy", "state": legacy_as_modern},
        ]

    def _write_both(
        self, mapping: TableMapping, record_id: str, modern_state: dict[str, Any], origin: Origin
    ) -> None:
        modern_rec = {**modern_state, MODERN_PK[mapping.table]: record_id}
        self.store.upsert_modern(mapping.table, modern_rec, origin)
        legacy_rec = apply_mapping(mapping, modern_state, "modern_to_legacy")
        legacy_rec = self._with_pk(mapping, legacy_rec, record_id, "modern_to_legacy")
        self.store.upsert_legacy(mapping.table, legacy_rec, origin)

    @staticmethod
    def _aware(ts: dt.datetime) -> dt.datetime:
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.UTC)

    def _row_ts(
        self, row: dict[str, Any], side: Literal["modern", "legacy"]
    ) -> dt.datetime | None:
        if side == "modern":
            return row.get("updated_at")
        raw = row.get("LAST_UPD_DT")
        if isinstance(raw, dt.datetime):
            return raw
        if isinstance(raw, dt.date):
            return dt.datetime.combine(raw, dt.time.min, tzinfo=dt.UTC)
        return None

    def _safe_get_modern(self, table: str, far_id: str) -> dict[str, Any] | None:
        """Lookup a modern row, returning None if far_id is empty or the
        far-side column type rejects it (e.g. non-UUID for a UUID PK).
        Rolls back the failed query so the connection stays usable."""
        if not far_id:
            return None
        try:
            return self.store.get_modern(table, far_id)
        except Exception:
            try:
                self.db.modern.rollback()
            except Exception:
                pass
            return None

    def _far_side_pk(
        self, mapping: TableMapping, incoming: dict[str, Any], direction: SyncDirection
    ) -> str:
        """Derive the far-side PK by applying the mapping's id field transform."""
        from .mappings import MappingError, apply_mapping

        try:
            mapped = apply_mapping(mapping, incoming, direction)
        except MappingError:
            mapped = {}
        pk = MODERN_PK[mapping.table] if direction == "legacy_to_modern" else LEGACY_PK[mapping.table]
        return str(mapped.get(pk, ""))

    def _with_pk(
        self,
        mapping: TableMapping,
        mapped: dict[str, Any],
        record_id: str,
        direction: SyncDirection,
    ) -> dict[str, Any]:
        pk = MODERN_PK[mapping.table] if direction == "legacy_to_modern" else LEGACY_PK[mapping.table]
        mapped.setdefault(pk, record_id)
        return mapped
