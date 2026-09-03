"""Engine contract tests — the negative cases the card pins:
duplicate event_id, clock skew, out-of-order, poison -> DLQ, conflict queue,
echo suppression, manual resolve writes both sides."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

from app.schemas import WebhookPayload

NOW = dt.datetime(2026, 9, 3, 12, 0, 0, tzinfo=dt.UTC)
ALICE = "00000000-0000-0000-0000-000000000001"
CUST = "C001"


def _modern_webhook(record: dict, event_id=None, ts=NOW) -> WebhookPayload:
    return WebhookPayload(
        event_id=event_id or uuid4(), table="customers", record=record,
        operation="update", source="modern", timestamp=ts,
    )


def _legacy_webhook(record: dict, event_id=None, ts=NOW) -> WebhookPayload:
    return WebhookPayload(
        event_id=event_id or uuid4(), table="customers", record=record,
        operation="update", source="legacy", timestamp=ts,
    )


def _modern_record(**over):
    base = {
        "id": ALICE, "email": "alice@acme.com", "name": "Alice Smith",
        "company": "Acme Corp", "status": "active", "updated_at": NOW,
    }
    return {**base, **over}


def _legacy_record(**over):
    base = {
        "CUST_ID": CUST, "CUST_NAME": "Alice Smith", "CUST_EMAIL": "alice@acme.com",
        "CUST_CO": "Acme Corp", "STATUS_CD": "A", "LAST_UPD_DT": NOW.date(),
    }
    return {**base, **over}


# ------------------------------------------------------------- idempotency


def test_duplicate_event_id_is_ignored(seeded_db, engine):
    eid = uuid4()
    first = engine.handle_webhook(_modern_webhook(_modern_record(name="A. Smith"), event_id=eid))
    second = engine.handle_webhook(_modern_webhook(_modern_record(name="A. Smith"), event_id=eid))
    assert first.status == "applied"
    assert second.status == "duplicate_ignored"
    # Only one audit row, one applied version bump.
    row = seeded_db.modern.execute(
        "SELECT count(*) AS n FROM audit_log WHERE event_id = %s", (eid,)
    ).fetchone()
    assert row["n"] == 1
    legacy = engine.store.get_legacy("customers", CUST)
    assert legacy["VERSION_NO"] == 2  # seed(1) + one apply


def test_x_event_id_header_overrides_body(client):
    eid = uuid4()
    payload = {
        "event_id": str(uuid4()), "table": "customers",
        "record": _modern_record(), "operation": "update",
        "source": "modern", "timestamp": NOW.isoformat(),
    }
    r1 = client.post("/api/webhooks/modern", json=payload, headers={"X-Event-ID": str(eid)})
    r2 = client.post("/api/webhooks/modern", json=payload, headers={"X-Event-ID": str(eid)})
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["event_id"] == str(eid)
    assert r2.json()["status"] == "duplicate_ignored"


# ------------------------------------------------------------- LWW + skew


def test_lww_newer_modern_wins_outside_tolerance(seeded_db, engine):
    # Legacy seeded LAST_UPD_DT = yesterday; incoming modern ts = now.
    out = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Fresh Modern"), ts=NOW)
    )
    assert out.status == "applied"
    legacy = engine.store.get_legacy("customers", CUST)
    assert legacy["CUST_NAME"] == "Fresh Modern"
    assert legacy["LAST_SYNCED_FROM"] == "sync:modern"


def test_lww_older_modern_loses(seeded_db, engine):
    # Push modern's updated_at far into the past: older than legacy's DATE.
    stale = NOW - dt.timedelta(days=30)
    out = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Stale Write"), ts=stale)
    )
    assert out.status == "no_change"
    legacy = engine.store.get_legacy("customers", CUST)
    assert legacy["CUST_NAME"] == "Alice Smith Updated"  # seed untouched


def test_clock_skew_within_tolerance_goes_to_source_of_truth(seeded_db, engine):
    # Seed legacy LAST_UPD_DT=yesterday-midnight vs incoming legacy ts=now is
    # outside tolerance, so first make them simultaneous: set legacy date to
    # today (same instant within 5s of the incoming modern ts).
    seeded_db.legacy.execute(
        "UPDATE legacy.CUST_MSTR SET LAST_UPD_DT = %s WHERE CUST_ID = %s",
        (NOW.date(), CUST),
    )
    incoming_ts = dt.datetime.combine(NOW.date(), dt.time(0, 0, 3), tzinfo=dt.UTC)
    # customers mapping: source_of_truth=modern -> legacy change inside the
    # 5s window must LOSE deterministically (not flap).
    out = engine.handle_webhook(
        _legacy_webhook(_legacy_record(CUST_NAME="Skewed Legacy"), ts=incoming_ts)
    )
    assert out.status == "no_change"
    modern = engine.store.get_modern("customers", ALICE)
    assert modern["name"] == "Alice Smith"  # modern seed untouched


def test_out_of_order_arrival_older_second(seeded_db, engine):
    newer = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Newest"), ts=NOW)
    )
    older = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Older"), ts=NOW - dt.timedelta(hours=2))
    )
    assert newer.status == "applied"
    # After the first apply, legacy.updated_at (LAST_UPD_DT) = today; the
    # older event is behind -> superseded.
    assert older.status == "no_change"
    assert engine.store.get_legacy("customers", CUST)["CUST_NAME"] == "Newest"


# ------------------------------------------------------------- poison / DLQ


def test_poison_record_goes_to_dead_letter_queue(seeded_db, engine):
    poison = _legacy_record(CUST_ID="C666", CUST_NAME="Bad Data Barry", CUST_EMAIL=None)
    out = engine.handle_webhook(_legacy_webhook(poison))
    assert out.status == "dead_lettered"
    dlq = seeded_db.modern.execute(
        "SELECT * FROM dead_letter_queue WHERE record_id = 'C666'"
    ).fetchone()
    assert dlq is not None
    assert "CUST_EMAIL" in dlq["error"] or "required field" in dlq["error"]
    audit = seeded_db.modern.execute(
        "SELECT status FROM audit_log WHERE event_id = %s", (out.event_id,)
    ).fetchone()
    assert audit["status"] == "dead_lettered"


def test_dlq_retry_backoff_grows(seeded_db, engine):
    poison = _legacy_record(CUST_ID="C666", CUST_EMAIL=None)
    eid = uuid4()
    engine.handle_webhook(_legacy_webhook(poison, event_id=eid))
    engine.store.dead_letter(eid, "customers", "C666", poison, "retry attempt")
    row = seeded_db.modern.execute(
        "SELECT retry_count, next_retry_at FROM dead_letter_queue WHERE event_id = %s", (eid,)
    ).fetchone()
    assert row["retry_count"] == 1
    assert row["next_retry_at"] > dt.datetime.now(dt.UTC) + dt.timedelta(seconds=55)


# ------------------------------------------------------------- conflicts


def test_inventory_change_queues_manual_review(seeded_db, engine):
    out = engine.poll_once("inventory", "legacy_to_modern")
    assert any(o.status == "conflict_queued" for o in out)
    conflicts = engine.store.list_conflicts("pending")
    inv = [c for c in conflicts if c["table_name"] == "inventory"]
    assert inv and inv[0]["record_id"] == "WIDGET-1"
    audit = seeded_db.modern.execute(
        "SELECT status, conflict_id FROM audit_log WHERE source_table = 'inventory'"
    ).fetchone()
    assert audit["status"] == "conflicted" and audit["conflict_id"] is not None


def test_resolve_conflict_writes_both_sides_and_closes(seeded_db, engine, client):
    engine.poll_once("inventory", "legacy_to_modern")
    conflict = engine.store.list_conflicts("pending")[0]
    chosen = {"product_id": "WIDGET-1", "quantity": 200, "last_restock": None}
    r = client.post(
        f"/api/conflicts/{conflict['conflict_id']}/resolve",
        json={"conflict_id": str(conflict["conflict_id"]), "chosen_state": chosen,
              "resolved_by": "pytest"},
    )
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert engine.store.get_modern("inventory", "WIDGET-1")["quantity"] == 200
    assert engine.store.get_legacy("inventory", "WIDGET-1")["QTY_OH"] == 200
    closed = engine.store.get_conflict(conflict["conflict_id"])
    assert closed["status"] == "resolved" and closed["resolved_by"] == "pytest"


def test_double_resolve_is_409(seeded_db, engine, client):
    engine.poll_once("inventory", "legacy_to_modern")
    conflict = engine.store.list_conflicts("pending")[0]
    body = {"conflict_id": str(conflict["conflict_id"]),
            "chosen_state": {"product_id": "WIDGET-1", "quantity": 1, "last_restock": None}}
    assert client.post(f"/api/conflicts/{conflict['conflict_id']}/resolve", json=body).status_code == 200
    assert client.post(f"/api/conflicts/{conflict['conflict_id']}/resolve", json=body).status_code == 409


# ------------------------------------------------------------- echo + poller


def test_echo_suppression_no_infinite_loop(seeded_db, engine):
    first = engine.poll_once("customers", "both")
    assert any(o.status == "applied" for o in first)
    second = engine.poll_once("customers", "both")
    # Nothing new should propagate: every row the second pass sees was
    # written by the sync engine itself (stamped) and must be skipped.
    assert all(o.status != "applied" for o in second)


def test_poller_uses_change_log_for_legacy(seeded_db, engine):
    out = engine.poll_once("customers", "legacy_to_modern")
    # C002 (legacy-only) must propagate to modern via CHANGE_LOG observation.
    assert any(o.status == "applied" for o in out)
    modern = engine.store.get_modern("customers", "C002")
    assert modern is not None and modern["email"] == "carol@hal.com"


def test_source_of_truth_wins_for_orders(seeded_db, engine):
    # orders mapping: source_of_truth=modern, strategy=source_of_truth_wins.
    # A legacy-side order change must be superseded.
    payload = WebhookPayload(
        event_id=uuid4(), table="orders",
        record={"ORD_ID": "ORD-001", "CUST_ID": "C001", "ORD_DT": NOW.date(),
                "ORD_AMT": "1.00", "ORD_STAT": "S"},
        operation="update", source="legacy", timestamp=NOW,
    )
    out = engine.handle_webhook(payload)
    assert out.status == "no_change"
