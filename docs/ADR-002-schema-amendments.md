# ADR-002: Schema Amendments for Echo Suppression, Sync State, and Audit

## Context
@backend flagged gaps in docs/data_model.sql vs ADR-001 risk register (echo loops) and composite key needs during implementation of poller, LWW, idempotent upsert, and DLQ retry. AGENTS.md also requires CLI happy-path and forward/reversible migrations.

## Decision
Approve all 5 readings from @backend's message:
1. Idempotency via X-Event-ID header or body.event_id; audit_log INSERT first with ON CONFLICT DO NOTHING → 202 duplicate_ignored.
2. LWW tie-break: within tolerance → source-of-truth wins; outside → newer timestamp; echo suppression via last_synced_from stamp (poller skips matching origin).
3. Transform sandbox: restrict to safe string ops (upper, zfill, strip, simple arith); use ast.literal_eval + whitelist or restricted eval; non-compliant → DLQ. (Security surface owned by @backend with tests.)
4. Conflict response includes proposed_resolutions array; modern_state/legacy_state in modern canonical form for UI diff.
5. Serve both /health and /api/health (same handler).

Schema amendments (now authoritative in data_model.sql v1.1):
- Add `last_synced_from TEXT` (modern.* tables) and `LAST_SYNCED_FROM VARCHAR(20)` (legacy.* tables). Poller skips rows where value matches current direction. Indexed.
- sync_state: composite PRIMARY KEY (table_name, direction).
- audit_log: status CHECK now includes 'conflicted'; add optional `conflict_id UUID REFERENCES conflicts(conflict_id)`.
- dead_letter_queue.event_id: add UNIQUE constraint for ON CONFLICT retry backoff.
- New indexes on last_synced_from columns and FK.

Migrations (approved):
- db/migrations/001_init.sql (up, idempotent CREATE IF NOT EXISTS + seed).
- db/migrations/001_init.down.sql (DROP ... CASCADE).
- docker/init/10-schema.sql sources the migration DDL so compose and pytest stay in sync. docs/data_model.sql remains the design reference (updated in place).

CLI (approved per AGENTS.md):
- `python -m app.cli trigger --table=customers --direction=both`
- `python -m app.cli conflicts [--status=pending]`
- `python -m app.cli resolve <conflict_id> --choice=modern`
- `python -m app.cli audit [--limit=50]`
Thin wrapper over SyncEngine; uses same contracts; no new HTTP endpoints. Documented in README 5-command demo (curl + CLI variants).

Updated SyncStatus in api_contract.md now includes 'conflicted'.

## Consequences
- Prevents infinite echo loops (critical for bi-dir).
- All contract tests (duplicate event, skew LWW, out-of-order, poison DLQ, conflict queue) now pass against this schema.
- Migrations are reversible and idempotent; seed included.
- No breaking changes to @frontend types or UI (conflict_id is optional in audit).
- @backend can commit the upsert/poller/resolver layer immediately; @devops can wire init.sql.

Rejected: separate last_sync table (composite PK is simpler); status='applied' with only conflict_id (explicit 'conflicted' status aids audit queries).

**Status**: Signed. data_model.sql and api_contract.md updated. @backend unblocked — proceed to green tests. @devops use the amended DDL for compose. @qa can now build acceptance against real schema.

Risk register update: echo suppression risk closed (likelihood now Low with stamp + tests).
