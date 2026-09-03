"""FastAPI application — exact endpoints from docs/api_contract.md v1.1.

Routes (all JSON, Pydantic v2):
    POST /api/sync/trigger
    GET  /api/conflicts?status=pending&table=customers
    POST /api/conflicts/{conflict_id}/resolve
    GET  /api/audit?limit=20&table=customers
    POST /api/webhooks/modern | /api/webhooks/legacy   (202, idempotent)
    GET  /health and /api/health                       (same handler, ADR-002 r5)
    GET  /api/mappings
    GET  /                                             (web/dist SPA, if built)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from .db import Database, DbConfig
from .engine import SyncEngine
from .mappings import MappingRegistry
from .migrate import migrate
from .schemas import (
    AuditEntry,
    Conflict,
    HealthResponse,
    MappingSummary,
    ResolveConflictRequest,
    SyncTriggerRequest,
    SyncTriggerResponse,
    WebhookPayload,
)

MAPPINGS_DIR = Path(__file__).resolve().parent.parent / "docs" / "mappings"
WEB_DIST = Path(
    os.environ.get("WEB_DIST", Path(__file__).resolve().parent.parent / "web" / "dist")
)


def build_engine() -> SyncEngine:
    db = Database(DbConfig.from_env())
    db.connect()
    mappings = MappingRegistry.load(MAPPINGS_DIR)
    return SyncEngine(db, mappings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    eng = build_engine()
    if os.environ.get("AUTO_MIGRATE", "1") == "1":
        migrate(eng.db, seed=os.environ.get("AUTO_SEED", "0") == "1")
    app.state.engine = eng
    yield
    eng.db.close()


app = FastAPI(title="legacy-sync-engine", version="0.1.0", lifespan=lifespan)


def eng(request: Request) -> SyncEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


# ---------------------------------------------------------------- admin / sync


@app.post("/api/sync/trigger", response_model=SyncTriggerResponse)
def trigger_sync(body: SyncTriggerRequest, request: Request) -> SyncTriggerResponse:
    outcomes = eng(request).poll_once(body.table, body.direction)
    summary = ", ".join(o.status for o in outcomes) or "no pending changes"
    return SyncTriggerResponse(
        job_id=outcomes[0].event_id if outcomes else uuid4(),
        status="completed",
        message=f"polled {body.table} ({body.direction}): {len(outcomes)} change(s) [{summary}]",
    )


@app.get("/api/conflicts", response_model=list[Conflict])
def list_conflicts(
    request: Request,
    status: str = Query("pending", pattern="^(pending|resolved)$"),
    table: str | None = None,
) -> list[Conflict]:
    engine = eng(request)
    out: list[Conflict] = []
    for r in engine.store.list_conflicts(status, table):
        mapping = engine.mappings.get(r["table_name"])
        proposed = engine.proposed_resolutions(
            mapping, r["modern_state"] or {}, r["legacy_state"] or {}
        )
        out.append(
            Conflict(
                conflict_id=r["conflict_id"],
                table_name=r["table_name"],
                record_id=r["record_id"],
                modern_state=r["modern_state"] or {},
                legacy_state=r["legacy_state"] or {},
                detected_at=r["detected_at"],
                strategy=r["resolution_strategy"],
                status=r["status"],
                proposed_resolutions=proposed,
            )
        )
    return out


@app.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: UUID, body: ResolveConflictRequest, request: Request) -> dict:
    try:
        outcome = eng(request).resolve_conflict(conflict_id, body.chosen_state, body.resolved_by)
    except KeyError:
        raise HTTPException(
            404, detail={"error": "NotFound", "detail": f"conflict {conflict_id} not found"}
        ) from None
    except ValueError as exc:
        raise HTTPException(
            409, detail={"error": "ConflictState", "detail": str(exc)}
        ) from None
    return {
        "status": outcome.status,
        "conflict_id": str(conflict_id),
        "event_id": str(outcome.event_id),
    }


@app.get("/api/audit", response_model=list[AuditEntry])
def list_audit(
    request: Request,
    limit: int = Query(20, ge=1, le=500),
    table: str | None = None,
) -> list[AuditEntry]:
    return eng(request).list_audit(limit, table)


# ---------------------------------------------------------------- webhooks


async def _webhook(
    payload: WebhookPayload, request: Request, x_event_id: UUID | None
) -> dict:
    if x_event_id is not None and x_event_id != payload.event_id:
        payload = payload.model_copy(update={"event_id": x_event_id})
    outcome = eng(request).handle_webhook(payload)
    body: dict = {"status": outcome.status, "event_id": str(outcome.event_id)}
    if outcome.conflict_id:
        body["conflict_id"] = str(outcome.conflict_id)
    if outcome.detail:
        body["detail"] = outcome.detail
    return body


@app.post("/api/webhooks/modern", status_code=202)
async def webhook_modern(
    payload: WebhookPayload,
    request: Request,
    x_event_id: UUID | None = Header(default=None),
) -> dict:
    if payload.source != "modern":
        raise HTTPException(
            400, detail={"error": "BadSource", "detail": "source must be 'modern'"}
        )
    return await _webhook(payload, request, x_event_id)


@app.post("/api/webhooks/legacy", status_code=202)
async def webhook_legacy(
    payload: WebhookPayload,
    request: Request,
    x_event_id: UUID | None = Header(default=None),
) -> dict:
    if payload.source != "legacy":
        raise HTTPException(
            400, detail={"error": "BadSource", "detail": "source must be 'legacy'"}
        )
    return await _webhook(payload, request, x_event_id)


# ---------------------------------------------------------------- observability


def _health(request: Request) -> HealthResponse:
    h = eng(request).health()
    return HealthResponse(
        status="ok" if h["modern_db"] == "up" and h["legacy_db"] == "up" else "degraded",
        modern_db=h["modern_db"],
        legacy_db=h["legacy_db"],
        last_sync=h["last_sync"],
        mapping_versions=h["mapping_versions"],
    )


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    return _health(request)


@app.get("/api/health", response_model=HealthResponse)
def api_health(request: Request) -> HealthResponse:
    return _health(request)


@app.get("/api/mappings", response_model=list[MappingSummary])
def list_mappings(request: Request) -> list[MappingSummary]:
    return [
        MappingSummary(
            version=m.version,
            table=m.table,
            source_of_truth=m.source_of_truth,
            conflict_strategy=m.conflict_strategy,
            clock_skew_tolerance_seconds=m.clock_skew_tolerance_seconds,
        )
        for m in eng(request).mappings.summaries()
    ]


# ---------------------------------------------------------------- SPA (optional)

if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="spa")
