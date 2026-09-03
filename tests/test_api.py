"""API contract tests — response shapes match api_contract.md v1.1 and the
fields web/src/types.ts consumes verbatim."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

NOW = dt.datetime(2026, 9, 3, 12, 0, 0, tzinfo=dt.UTC)


def test_health_shape_both_paths(client):
    for path in ("/health", "/api/health"):
        r = client.get(path)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("ok", "degraded")
        assert body["modern_db"] == "up" and body["legacy_db"] == "up"
        assert isinstance(body["last_sync"], dict)
        assert body["mapping_versions"] == ["1.0"]


def test_mappings_shape(client):
    r = client.get("/api/mappings")
    assert r.status_code == 200
    tables = {m["table"] for m in r.json()}
    assert tables == {"customers", "orders", "inventory"}
    for m in r.json():
        assert set(m) == {"version", "table", "source_of_truth",
                          "conflict_strategy", "clock_skew_tolerance_seconds"}


def test_trigger_sync_shape(client):
    r = client.post("/api/sync/trigger", json={"table": "customers", "direction": "both"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"job_id", "status", "message"}
    assert body["status"] == "completed"


def test_conflicts_shape_after_queue(seeded_engine, client):
    seeded_engine.poll_once("inventory", "legacy_to_modern")
    r = client.get("/api/conflicts?status=pending")
    assert r.status_code == 200
    conflict = r.json()[0]
    assert set(conflict) == {
        "conflict_id", "table_name", "record_id", "modern_state", "legacy_state",
        "detected_at", "strategy", "status", "proposed_resolutions",
    }
    assert conflict["strategy"] == "manual_review"
    names = {p["name"] for p in conflict["proposed_resolutions"]}
    assert names == {"modern", "legacy"}
    # Modern-canonical: legacy proposal carries modern field names.
    legacy_opt = next(p for p in conflict["proposed_resolutions"] if p["name"] == "legacy")
    assert "quantity" in legacy_opt["state"]


def test_audit_shape(seeded_engine, client):
    seeded_engine.poll_once("customers", "modern_to_legacy")
    r = client.get("/api/audit?limit=10")
    assert r.status_code == 200
    assert r.json(), "audit must not be empty after a sync"
    entry = r.json()[0]
    assert set(entry) == {"event_id", "created_at", "direction", "table_name",
                          "record_id", "resolution_strategy", "status", "before", "after"}
    assert entry["status"] in ("applied", "conflicted", "dead_lettered")


def test_webhook_bad_source_rejected(client):
    r = client.post(
        "/api/webhooks/modern",
        json={"event_id": str(uuid4()), "table": "customers", "record": {},
              "operation": "update", "source": "legacy", "timestamp": NOW.isoformat()},
    )
    assert r.status_code == 400


def test_audit_filter_by_table(seeded_engine, client):
    seeded_engine.poll_once("customers", "modern_to_legacy")
    r = client.get("/api/audit?table=customers")
    assert all(e["table_name"] == "customers" for e in r.json())
