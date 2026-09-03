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
SyncStatus = Literal["pending", "applied", "failed", "dead_lettered"]

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

## Implementation Notes for @backend
1. Implement `SyncEngine` with the exact method signatures implied above.
2. Load mappings from `docs/mappings/*.yaml` at startup with validation.
3. Poller runs every 5s (configurable); uses `sync_state` table.
4. For legacy without timestamps, rely on `CHANGE_LOG`.
5. Simulate clock skew in `tests/test_conflicts.py` by manually setting `updated_at` in past/future.
6. Contract tests must roundtrip a record through mapping in both directions and survive conflict scenarios.
7. Seed data via the `seed_dirty_data()` function on startup for demo.

This contract is binding. Changes require new ADR and @architect signoff. @frontend must consume these exact response shapes for the conflict queue UI. @qa will test the negative cases listed in ADR-001.

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
