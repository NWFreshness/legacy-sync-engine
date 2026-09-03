# API & Interface Contract for legacy-sync-engine
**Version**: 1.0 (signed off — @backend implements against these exact shapes)

All models use **Pydantic v2**. FastAPI will expose OpenAPI at `/docs`.

## Core Types (schemas/sync.py)

```python
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Literal, Dict, Any, Optional
from uuid import UUID

SyncDirection = Literal["modern_to_legacy", "legacy_to_modern"]
ResolutionStrategy = Literal["last_write_wins", "source_of_truth_wins", "manual_review"]
SyncStatus = Literal["pending", "applied", "conflicted", "failed", "dead_lettered"]

class MappingField(BaseModel):
    modern_field: str
    legacy_field: str
    type: str  # "string", "integer", "datetime", "numeric"
    direction: Literal["both", "modern_to_legacy", "legacy_to_modern"]
    required: bool = False
    transform: Optional[Dict[str, str]] = None  # e.g. {"modern_to_legacy": "str(value).zfill(10)"}
    default: Optional[Any] = None

class TableMapping(BaseModel):
    version: str
    table: str
    source_of_truth: Literal["modern", "legacy", "both"]
    conflict_strategy: ResolutionStrategy
    clock_skew_tolerance_seconds: int = 5
    fields: list[MappingField]

class SyncEvent(BaseModel):
    event_id: UUID
    direction: SyncDirection
    table_name: str
    record_id: str
    before: Dict[str, Any]
    after: Dict[str, Any]
    mapping_version: str
    strategy: ResolutionStrategy
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class Conflict(BaseModel):
    conflict_id: UUID
    table_name: str
    record_id: str
    modern_state: Dict[str, Any]
    legacy_state: Dict[str, Any]
    detected_at: datetime
    strategy: ResolutionStrategy = "manual_review"
    status: Literal["pending", "resolved"] = "pending"
    proposed_resolutions: list[Dict[str, Any]] = Field(default_factory=list)

class ResolveConflictRequest(BaseModel):
    conflict_id: UUID
    chosen_state: Dict[str, Any]  # the winning payload
    resolution_notes: Optional[str] = None
    resolved_by: str = "admin"

class WebhookPayload(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)  # idempotency key
    table: str
    record: Dict[str, Any]
    operation: Literal["insert", "update", "delete"]
    source: Literal["modern", "legacy"]
    timestamp: datetime

# Admin API Responses
class SyncTriggerResponse(BaseModel):
    job_id: UUID
    status: str
    message: str

class AuditEntry(BaseModel):
    event_id: UUID
    created_at: datetime
    direction: SyncDirection
    table_name: str
    record_id: str
    resolution_strategy: ResolutionStrategy
    status: SyncStatus
    before: Optional[Dict[str, Any]]
    after: Optional[Dict[str, Any]]
```

## FastAPI Endpoints (main.py routes — exact signatures)

### Admin / Sync
- `POST /api/sync/trigger`
  - Body: `{ "table": "customers", "direction": "both" }`
  - Returns: `SyncTriggerResponse`
  - Idempotent if same job_id passed.

- `GET /api/conflicts?status=pending&table=customers`
  - Returns: `list[Conflict]`

- `POST /api/conflicts/{conflict_id}/resolve`
  - Body: `ResolveConflictRequest`
  - Side effect: Applies chosen_state to both DBs, logs to audit, clears conflict.

- `GET /api/audit?limit=20&table=customers`
  - Returns: `list[AuditEntry]`

### Webhooks (for real-time from either side)
- `POST /api/webhooks/modern`
- `POST /api/webhooks/legacy`
  - Body: `WebhookPayload`
  - Headers: `X-Event-ID` (for idempotency)
  - Returns 202 Accepted immediately; processing async or in transaction.
  - Must be idempotent: same event_id is ignored after first success.

### Observability
- `GET /health` — returns DB connectivity + last sync times.
- `GET /api/mappings` — lists loaded mapping versions.

## Error Contract
All errors return:
```json
{
  "error": "ConflictDetected",
  "detail": "...",
  "conflict_id": "uuid-here"
}
```
Standard HTTP: 400 bad mapping, 409 conflict (if not using queue), 429 rate limit on webhooks, 500 with dead-letter reference.

## Idempotency Guarantees
- All writes check `event_id` in audit_log first.
- Upsert uses `version` column + `ON CONFLICT DO UPDATE ... WHERE version = excluded.version - 1`.

## Implementation Notes for @backend (updated per ADR-002)
1. All 5 readings approved: idempotency (header/body event_id → audit INSERT first), LWW tie-break (tolerance → source-of-truth, else newer ts), transform sandbox (safe string/arith only; DLQ on violation), Conflict model with proposed_resolutions + modern-canonical states, dual /health + /api/health.
2. Echo suppression via new last_synced_from columns (poller skips matching origin); composite PK on sync_state; audit status includes 'conflicted' + optional conflict_id FK; UNIQUE on dead_letter_queue.event_id. See updated data_model.sql + ADR-002.
3. Migrations: db/migrations/001_init.sql (up + seed, idempotent) + 001_init.down.sql; docker/init/10-schema.sql sources it. docs/data_model.sql is design reference (amended).
4. CLI: approved per AGENTS.md — `python -m app.cli trigger|conflicts|resolve|audit` thin wrapper over SyncEngine (curl variant also in README). No new HTTP surface.
5. Poller 5s default, CHANGE_LOG for legacy, skew simulation in tests, full contract tests for duplicate/skew/out-of-order/DLQ/conflict. Seed via seed_dirty_data().

This contract (with ADR-002 amendments) is binding. @backend unblocked for implementation. @frontend shapes unchanged. @qa/@devops use amended DDL. Changes require new ADR.

**Sequence Diagram** (Mermaid — see docs/sequence.mmd):
```
sequenceDiagram
    participant User
    participant ModernDB
    participant LegacyDB
    participant SyncEngine
    participant ConflictQueue
    participant AuditLog

    User->>ModernDB: UPDATE customers...
    ModernDB->>SyncEngine: Webhook or Poller detects
    SyncEngine->>LegacyDB: Apply mapping + upsert
    alt Conflict Detected
        SyncEngine->>ConflictQueue: Queue with both states
        User->>AdminUI: GET /conflicts
        User->>AdminUI: POST /resolve with choice
        AdminUI->>SyncEngine: Apply resolution
    else No Conflict
        SyncEngine->>AuditLog: Log before/after
    end
    SyncEngine->>LegacyDB: Webhook push (reverse direction)
```
