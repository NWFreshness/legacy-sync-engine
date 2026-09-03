# ADR-001: Architecture for legacy-sync-engine

## Context
We are building a clone-and-run portfolio demo showing bi-directional sync between a modern SaaS Postgres (`modern_saas`) and a "legacy ERP" simulated by Postgres with ugly column names, missing timestamps, and inconsistent data types (`legacy_erp`). 

The system must support:
- Change Data Capture via poller + webhook receiver (either side can initiate changes).
- Versioned YAML field mappings (legacy <-> modern).
- Multiple conflict resolution strategies: last-write-wins (with clock-skew handling), source-of-truth-wins, manual-review queue.
- Idempotent operations, dead-letter queue for poison pills, retry with exponential backoff.
- Comprehensive audit log of every sync event (before/after payloads).
- Simple FastAPI admin API (and later UI) for triggering syncs, viewing/resolving conflicts.
- Tests simulating clock skew and out-of-order updates.

Constraints from AGENTS.md:
- Python 3.12+, FastAPI, Docker Compose, Postgres only.
- Local mocks only (no real SQL Server, no paid services).
- Must boot and demo in <10 minutes.
- Small, correct v1 — no half-finished enterprise features.

## Decision
We adopt a **poller-driven bidirectional sync engine with explicit mapping and pluggable conflict resolution**.

### Core Components & Boundaries
1. **Databases** (docker-compose services):
   - `modern_saas`: Normalized tables (`customers`, `orders`, `inventory`) with proper `updated_at`, UUID PKs, JSONB for flexible fields.
   - `legacy_erp`: "Legacy" tables (`CUST_MSTR`, `ORD_HDR`, `INV_BAL`) with `CUST_ID`, `ORD_DT`, `QTY_OH` style columns, some without timestamps (uses separate `CHANGE_LOG` table for CDC).

2. **Mapping Layer** (`sync/mappings/`):
   - Versioned YAML files (`v1_customer_mapping.yaml`, etc.).
   - Each mapping defines: source_field, target_field, type_cast, default, transform (simple Python expressions or registered functions).
   - Loaded at startup; version pinned per sync job.

3. **Sync Engine** (`sync/core/engine.py` - interface owned by @architect):
   - `SyncEngine` class with `sync_table(direction: Literal['modern_to_legacy', 'legacy_to_modern'], table: str, since: datetime)`.
   - Uses poller (queries `updated_at` or CHANGE_LOG).
   - Webhook receivers (`/webhooks/modern`, `/webhooks/legacy`) for real-time push.
   - Applies mappings bidirectionally.
   - Detects conflicts by comparing last_sync_version or checksum.

4. **Conflict Resolution** (`sync/conflicts/resolver.py`):
   - Strategies implemented as pluggable classes:
     - `LastWriteWinsResolver` (compares `updated_at`, tolerates 5s clock skew).
     - `SourceOfTruthResolver` (modern_saas always wins).
     - `ManualReviewResolver` (queues in `conflicts` table).
   - Configurable per-table in mapping YAML.

5. **Persistence**:
   - `audit_log` table: event_id, timestamp, direction, table, record_id, before_state (JSONB), after_state, resolution_strategy, status.
   - `dead_letter_queue`: failed records with error, retry_count, next_retry.
   - `conflicts`: pending manual resolutions with proposed_state options.
   - `sync_state`: last successful sync per table/direction for pollers.

6. **API Contract** (FastAPI, OpenAPI):
   - Admin: `POST /sync/trigger`, `GET /conflicts`, `POST /conflicts/{id}/resolve`.
   - Webhooks: `POST /webhooks/{source}` (idempotent via `X-Event-ID` header).
   - Status: `GET /health`, `GET /audit?limit=50`.
   - All endpoints use Pydantic v2 models (see `schemas/`).

7. **Idempotency & Reliability**:
   - Every change gets unique `event_id` (UUID).
   - Upserts use `ON CONFLICT` with version check.
   - Retry queue with exponential backoff (max 5 attempts).
   - Dead-letter after failures.

8. **Testing**:
   - Unit: mapping application, conflict detection.
   - Integration: seeded dirty data, simulate out-of-order/clock-skew updates, verify resolution.
   - Happy-path: update on each side, assert correct merge or queued conflict.

Tech choices:
- SQLAlchemy 2.0 + Alembic for migrations (but v1 uses raw SQL + psycopg for simplicity in demo).
- Pydantic for all contracts.
- YAML + strict schema validation for mappings (using pydantic-yaml or strictdict).
- No ORM in legacy path to simulate "ugly" SQL.

**Rejected Alternatives**:
- Real Debezium + Kafka: too heavy for 10-min clone, requires extra services.
- Real SQL Server in Docker: license/complexity issues; Postgres with ugly names is sufficient mock.
- Full CDC with triggers on every table: overkill for demo; poller + optional webhook is clearer.
- AI-based conflict resolution: out of scope for v1 (could be v2).
- Event sourcing: adds complexity without demo value.

## Consequences
- Positive: Clear boundaries (@backend implements against exact mapping YAML schema and SyncEngine interface). Easy to demo conflicts. Local-only.
- Negative: Poller has latency (5s default); not true real-time (webhooks mitigate). Clock skew simulation in tests must be careful.
- Migration: Initial `docker-compose up` runs init.sql seeds + migrations.
- Observability: All syncs logged to audit table + structured console JSON.

## Risks & Owners (updated register)
| Risk | Likelihood | Impact | Owner | Mitigation |
|------|------------|--------|-------|------------|
| Clock skew breaks LWW | Medium | High | @architect | Explicit 5s tolerance window + test harness that injects skew |
| Mapping drift between YAML and DB schemas | Low | High | @backend | Schema validation on startup + contract tests |
| Poison records overwhelm retry | Low | Medium | @backend | Dead-letter after 5 retries; admin UI to inspect/replay |
| Bidirectional loops (echo) | High | High | @architect | Record `last_synced_from` and ignore if matches origin |
| UI invents payload shapes | Medium | Medium | @architect | Strict OpenAPI spec + contract tests enforced by @reviewer |

**Status**: Approved. Interfaces signed off. @backend may now implement against `schemas/sync.py` and mapping examples. No stack fights.

Next: @devops for docker-compose.yml shape review (Postgres x2 + FastAPI + optional simple frontend).
