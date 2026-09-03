-- legacy-sync-engine/db/migrations/001_init.sql
-- Migration of record (ADR-002). Idempotent: safe to re-run.
-- Design reference: docs/data_model.sql v1.1 (authoritative amendments).
--
-- Applies to a single database; compose runs it against BOTH modern_saas and
-- legacy_erp so each side carries its own sync infrastructure for the demo
-- (audit/DLQ/conflicts are written to whichever DB the app is configured to
-- treat as "modern" — see MODERN_DSN/LEGACY_DSN).

BEGIN;

-- =============================================
-- MODERN schema (skipped silently if this DB is the legacy side)
-- =============================================
CREATE SCHEMA IF NOT EXISTS modern;

CREATE TABLE IF NOT EXISTS modern.customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    company TEXT,
    status TEXT DEFAULT 'active',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    version INTEGER DEFAULT 1,
    last_synced_from TEXT
);

CREATE TABLE IF NOT EXISTS modern.orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES modern.customers(id),
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(12,2) NOT NULL,
    status TEXT DEFAULT 'pending',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    version INTEGER DEFAULT 1,
    last_synced_from TEXT
);

CREATE TABLE IF NOT EXISTS modern.inventory (
    product_id TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL DEFAULT 0,
    last_restock TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    version INTEGER DEFAULT 1,
    last_synced_from TEXT
);

-- =============================================
-- LEGACY schema (ugly names, inconsistent types, some missing timestamps)
-- =============================================
CREATE SCHEMA IF NOT EXISTS legacy;

CREATE TABLE IF NOT EXISTS legacy.CUST_MSTR (
    CUST_ID VARCHAR(20) PRIMARY KEY,
    CUST_NAME VARCHAR(100) NOT NULL,
    CUST_EMAIL VARCHAR(80),
    CUST_CO VARCHAR(50),
    STATUS_CD CHAR(1) DEFAULT 'A',
    LAST_UPD_DT DATE,
    VERSION_NO INTEGER DEFAULT 1,
    LAST_SYNCED_FROM VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS legacy.ORD_HDR (
    ORD_ID VARCHAR(20) PRIMARY KEY,
    CUST_ID VARCHAR(20) REFERENCES legacy.CUST_MSTR(CUST_ID),
    ORD_DT DATE NOT NULL,
    ORD_AMT NUMERIC(10,2),
    ORD_STAT CHAR(1),
    VERSION_NO INTEGER DEFAULT 1,
    LAST_SYNCED_FROM VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS legacy.INV_BAL (
    PROD_CD VARCHAR(20) PRIMARY KEY,
    QTY_OH INTEGER NOT NULL,
    LAST_REPL_DT DATE,
    VERSION_NO INTEGER DEFAULT 1,
    LAST_SYNCED_FROM VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS legacy.CHANGE_LOG (
    log_id SERIAL PRIMARY KEY,
    table_name VARCHAR(30) NOT NULL,
    record_id VARCHAR(50) NOT NULL,
    operation CHAR(1) NOT NULL,
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    payload JSONB
);

-- =============================================
-- SYNC INFRASTRUCTURE (ADR-002 amendments applied)
-- =============================================
CREATE TABLE IF NOT EXISTS sync_state (
    table_name TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('modern_to_legacy', 'legacy_to_modern')),
    last_sync_ts TIMESTAMPTZ,
    last_event_id UUID,
    version INTEGER DEFAULT 1,
    PRIMARY KEY (table_name, direction)
);

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    table_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    modern_state JSONB,
    legacy_state JSONB,
    detected_at TIMESTAMPTZ,
    resolution_strategy TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'ignored')),
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    final_state JSONB
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    direction TEXT NOT NULL,
    source_table TEXT NOT NULL,
    record_id TEXT NOT NULL,
    before_state JSONB,
    after_state JSONB,
    mapping_version TEXT,
    resolution_strategy TEXT,
    status TEXT DEFAULT 'applied' CHECK (status IN ('pending', 'applied', 'conflicted', 'failed', 'dead_lettered')),
    error_message TEXT,
    conflict_id UUID REFERENCES conflicts(conflict_id)
);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
    dlq_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES audit_log(event_id) UNIQUE,
    table_name TEXT,
    record_id TEXT,
    payload JSONB,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_record ON audit_log(source_table, record_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_conflict ON audit_log(conflict_id);
CREATE INDEX IF NOT EXISTS idx_last_synced_modern ON modern.customers(last_synced_from);
CREATE INDEX IF NOT EXISTS idx_last_synced_orders ON modern.orders(last_synced_from);
CREATE INDEX IF NOT EXISTS idx_last_synced_inventory ON modern.inventory(last_synced_from);
CREATE INDEX IF NOT EXISTS idx_last_synced_legacy_cust ON legacy.CUST_MSTR(LAST_SYNCED_FROM);
CREATE INDEX IF NOT EXISTS idx_last_synced_legacy_ord ON legacy.ORD_HDR(LAST_SYNCED_FROM);
CREATE INDEX IF NOT EXISTS idx_last_synced_legacy_inv ON legacy.INV_BAL(LAST_SYNCED_FROM);
CREATE INDEX IF NOT EXISTS idx_conflicts_pending ON conflicts(status, table_name);
CREATE INDEX IF NOT EXISTS idx_change_log ON legacy.CHANGE_LOG(table_name, changed_at);

COMMIT;
