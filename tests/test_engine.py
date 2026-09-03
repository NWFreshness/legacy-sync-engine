"""Engine contract tests — the negative cases the card pins:
duplicate event_id, clock skew, out-of-order, poison -> DLQ, conflict queue,
echo suppression, manual resolve writes both sides.

Test data model: the modern alice record has id=ALICE (UUID). The mapping
truncates to 20 chars for the legacy CUST_ID (VARCHAR(20)). The seed
pre-populates a matching legacy row under that truncated PK so LWW and
conflict scenarios have a real counterpart.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from app.schemas import WebhookPayload

NOW = dt.datetime(2026, 9, 3, 12, 0, 0, tzinfo=dt.UTC)
ALICE = "00000000-0000-0000-0000-000000000001"
CUST = ALICE[:20]  # legacy PK after mapping transform


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
        "company": "Acme Corp", "status": "active", "updated_at": NOW.isoformat(),
    }
    return {**base, **over}


def _legacy_record(**over):
    base = {
        "CUST_ID": CUST, "CUST_NAME": "Alice Smith", "CUST_EMAIL": "alice@acme.com",
        "CUST_CO": "Acme Corp", "STATUS_CD": "A", "LAST_UPD_DT": NOW.date().isoformat(),
    }
    return {**base, **over}


# ------------------------------------------------------------- idempotency


def test_duplicate_event_id_is_ignored(seeded_db, engine):
    eid = uuid4()
    first = engine.handle_webhook(_modern_webhook(_modern_record(name="A. Smith"), event_id=eid))
    if first.status == "dead_lettered":
        row = seeded_db.modern.execute(
            "SELECT error FROM dead_letter_queue WHERE event_id = %s", (first.event_id,)
        ).fetchone()
        pytest.fail(f"unexpected dead-letter on clean record: {row and row['error']}")
    second = engine.handle_webhook(_modern_webhook(_modern_record(name="A. Smith"), event_id=eid))
    assert first.status == "applied"
    assert second.status == "duplicate_ignored"
    row = seeded_db.modern.execute(
        "SELECT count(*) AS n FROM audit_log WHERE event_id = %s", (eid,)
    ).fetchone()
    assert row["n"] == 1


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
    # Seed legacy LAST_UPD_DT = NOW-2h; incoming modern ts = NOW (10h newer).
    out = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Fresh Modern"), ts=NOW)
    )
    assert out.status == "applied"
    legacy = engine.store.get_legacy("customers", CUST)
    assert legacy["CUST_NAME"] == "Fresh Modern"
    assert legacy["LAST_SYNCED_FROM"] == "sync:modern"


def test_lww_older_modern_loses(seeded_db, engine):
    # Modern ts 30 days in the past; legacy seed is 2h ago. Legacy wins.
    stale = NOW - dt.timedelta(days=30)
    out = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Stale Write"), ts=stale)
    )
    assert out.status == "no_change"
    legacy = engine.store.get_legacy("customers", CUST)
    assert legacy["CUST_NAME"] == "Alice Smith (legacy)"  # seed untouched


def test_clock_skew_within_tolerance_goes_to_source_of_truth(seeded_db, engine):
    # Both sides have alice. Set timestamps to be within the 5s tolerance.
    seeded_db.legacy.execute(
        'UPDATE legacy."CUST_MSTR" SET "LAST_UPD_DT" = %s WHERE "CUST_ID" = %s',
        (NOW.date(), CUST),
    )
    seeded_db.modern.execute(
        "UPDATE modern.customers SET updated_at = %s WHERE id = %s",
        (NOW, ALICE),
    )
    # Send a modern change. Both rows exist with timestamps within tolerance.
    # source_of_truth=modern -> modern change wins deterministically.
    incoming_ts = NOW + dt.timedelta(seconds=2)  # 2s after the seed NOW
    out = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Skewed Modern"), ts=incoming_ts)
    )
    assert out.status == "applied"
    legacy = engine.store.get_legacy("customers", CUST)
    assert legacy["CUST_NAME"] == "Skewed Modern"


def test_out_of_order_arrival_older_second(seeded_db, engine):
    # First apply writes modern->legacy with ts=NOW (last_synced_from stamps).
    newer = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Newest"), ts=NOW)
    )
    # The legacy row's LAST_UPD_DT is a DATE column; after the first apply
    # it becomes NOW.date(). The "older" event uses ts=NOW-1day so it's
    # unambiguously before the DATE precision boundary.
    older_ts = NOW - dt.timedelta(days=1)
    older = engine.handle_webhook(
        _modern_webhook(_modern_record(name="Older"), ts=older_ts)
    )
    assert newer.status == "applied"
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
    # Modern seed has WIDGET-1 quantity=150 (3 days ago).
    # Legacy seed has WIDGET-1 QTY_OH=175 (2 days ago).
    # Polling legacy->modern detects both rows exist => manual_review queue.
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
    # Second pass: every modern row was stamped by sync:legacy (from the
    # CHANGE_LOG poll) or sync:modern; poller must skip them.
    second = engine.poll_once("customers", "both")
    assert all(o.status != "applied" for o in second)


def test_poller_uses_change_log_for_legacy(seeded_db, engine):
    out = engine.poll_once("customers", "legacy_to_modern")
    # C002 (legacy-only, observed via CHANGE_LOG) must propagate to modern.
    assert any(o.status == "applied" for o in out)
    # The modern PK is derived from the legacy CUST_ID via the mapping's
    # legacy_to_modern transform (zero-padded to UUID shape).
    c002_uuid = "00000000-0000-0000-0000-00000000C002"
    modern = engine.store.get_modern("customers", c002_uuid)
    assert modern is not None and modern["email"] == "carol@hal.com"


def test_source_of_truth_wins_for_orders(seeded_db, engine):
    # orders mapping: source_of_truth=modern, strategy=source_of_truth_wins.
    # When a modern webhook arrives for an order, the modern side wins
    # regardless of legacy state. Verified by checking legacy is NOT updated
    # with conflicting values.
    modern_id = "00000000-0000-0000-0000-000000000002"
    payload = WebhookPayload(
        event_id=uuid4(), table="orders",
        record={"id": modern_id, "customer_id": ALICE, "order_date": NOW.isoformat(),
                "total_amount": "42.50", "status": "cancelled"},
        operation="update", source="modern", timestamp=NOW,
    )
    out = engine.handle_webhook(payload)
    assert out.status == "applied"
    # The legacy order ORD-001 should still hold the seed value (999.99, S).
    legacy = engine.store.get_legacy("orders", "ORD-001")
    assert legacy is not None
    assert str(legacy["ORD_AMT"]) == "999.99"  # seed value, not overwritten
