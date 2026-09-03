-- legacy-sync-engine/docs/data_model.sql
-- Initial schema for both databases + sync infrastructure.
-- Run via init scripts in docker-compose.

-- =============================================
-- MODERN_SAAS (normalized, proper types, timestamps)
-- =============================================
CREATE SCHEMA IF NOT EXISTS modern;

CREATE TABLE modern.customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    company TEXT,
    status TEXT DEFAULT 'active',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    version INTEGER DEFAULT 1,
    last_synced_from TEXT  -- echo suppression: 'sync:modern', 'sync:legacy', 'admin', NULL
);

CREATE TABLE modern.orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES modern.customers(id),
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(12,2) NOT NULL,
    status TEXT DEFAULT 'pending',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    version INTEGER DEFAULT 1,
    last_synced_from TEXT  -- echo suppression
);

CREATE TABLE modern.inventory (
    product_id TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL DEFAULT 0,
    last_restock TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    version INTEGER DEFAULT 1
);

-- =============================================
-- LEGACY_ERP (ugly names, inconsistent types, some missing timestamps)
-- =============================================
CREATE SCHEMA IF NOT EXISTS legacy;

CREATE TABLE legacy.CUST_MSTR (
    CUST_ID VARCHAR(20) PRIMARY KEY,  -- string PK to simulate old system
    CUST_NAME VARCHAR(100) NOT NULL,
    CUST_EMAIL VARCHAR(80),
    CUST_CO VARCHAR(50),
    STATUS_CD CHAR(1) DEFAULT 'A',    -- inconsistent type
    LAST_UPD_DT DATE,                 -- DATE not TIMESTAMPTZ on some
    VERSION_NO INTEGER DEFAULT 1,
    LAST_SYNCED_FROM VARCHAR(20)  -- echo suppression: 'sync:modern', 'sync:legacy', 'admin', NULL
);

CREATE TABLE legacy.ORD_HDR (
    ORD_ID VARCHAR(20) PRIMARY KEY,
    CUST_ID VARCHAR(20) REFERENCES legacy.CUST_MSTR(CUST_ID),
    ORD_DT DATE NOT NULL,             -- old date format
    ORD_AMT NUMERIC(10,2),
    ORD_STAT CHAR(1),
    VERSION_NO INTEGER DEFAULT 1,
    LAST_SYNCED_FROM VARCHAR(20)  -- echo suppression
    -- Note: no updated_at on purpose for conflict testing
);

CREATE TABLE legacy.INV_BAL (
    PROD_CD VARCHAR(20) PRIMARY KEY,
    QTY_OH INTEGER NOT NULL,
    LAST_REPL_DT DATE,
    VERSION_NO INTEGER DEFAULT 1,
    LAST_SYNCED_FROM VARCHAR(20)  -- echo suppression
);

-- Change log for legacy tables without timestamps (simulates CDC)
CREATE TABLE legacy.CHANGE_LOG (
    log_id SERIAL PRIMARY KEY,
    table_name VARCHAR(30) NOT NULL,
    record_id VARCHAR(50) NOT NULL,
    operation CHAR(1) NOT NULL,  -- I/U/D
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    payload JSONB
);

-- =============================================
-- SYNC INFRASTRUCTURE (shared or in modern schema)
-- =============================================
CREATE TABLE IF NOT EXISTS sync_state (
    table_name TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('modern_to_legacy', 'legacy_to_modern')),
    last_sync_ts TIMESTAMPTZ,
    last_event_id UUID,
    version INTEGER DEFAULT 1,
    PRIMARY KEY (table_name, direction)  -- per-table+direction watermark
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
    status TEXT DEFAULT 'applied' CHECK (status IN ('applied', 'conflicted', 'failed', 'dead_lettered')),
    error_message TEXT,
    conflict_id UUID REFERENCES conflicts(conflict_id)  -- populated on queued conflicts
);

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    table_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    modern_state JSONB,
    legacy_state JSONB,
    detected_at TIMESTAMPTZ,
    resolution_strategy TEXT,  -- 'manual_review'
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'ignored')),
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    final_state JSONB
);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
    dlq_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES audit_log(event_id) UNIQUE,  -- for retry upsert by event
    table_name TEXT,
    record_id TEXT,
    payload JSONB,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance on pollers + echo suppression + conflict FK
CREATE INDEX idx_audit_record ON audit_log(source_table, record_id, created_at);
CREATE INDEX idx_audit_conflict ON audit_log(conflict_id);
CREATE INDEX idx_sync_last ON sync_state(table_name, direction);
CREATE INDEX idx_last_synced_modern ON modern.customers(last_synced_from);
CREATE INDEX idx_last_synced_orders ON modern.orders(last_synced_from);
CREATE INDEX idx_last_synced_inventory ON modern.inventory(last_synced_from);
CREATE INDEX idx_last_synced_legacy_cust ON legacy.CUST_MSTR(LAST_SYNCED_FROM);
CREATE INDEX idx_last_synced_legacy_ord ON legacy.ORD_HDR(LAST_SYNCED_FROM);
CREATE INDEX idx_last_synced_legacy_inv ON legacy.INV_BAL(LAST_SYNCED_FROM);
CREATE INDEX idx_conflicts_pending ON conflicts(status, table_name);
CREATE INDEX idx_change_log ON legacy.CHANGE_LOG(table_name, changed_at);

-- Seed function for dirty data (overlapping records)
CREATE OR REPLACE FUNCTION seed_dirty_data() RETURNS void AS $$
BEGIN
    -- Modern customer that conflicts with legacy
    INSERT INTO modern.customers (id, email, name, company, status, updated_at, last_synced_from)
    VALUES ('00000000-0000-0000-0000-000000000001'::uuid, 'alice@acme.com', 'Alice Smith', 'Acme Corp', 'active', NOW() - INTERVAL '1 hour', NULL)
    ON CONFLICT (id) DO NOTHING;

    -- Legacy version with different data (to trigger conflict)
    INSERT INTO legacy.CUST_MSTR (CUST_ID, CUST_NAME, CUST_EMAIL, CUST_CO, STATUS_CD, LAST_UPD_DT, LAST_SYNCED_FROM)
    VALUES ('C001', 'Alice Smith Updated', 'alice.new@acme.com', 'ACME INC', 'A', CURRENT_DATE - 1, NULL)
    ON CONFLICT (CUST_ID) DO NOTHING;

    -- Similar for orders and inventory...
    INSERT INTO modern.orders (id, customer_id, order_date, total_amount, status, last_synced_from)
    VALUES ('00000000-0000-0000-0000-000000000002'::uuid, '00000000-0000-0000-0000-000000000001'::uuid, NOW(), 1250.00, 'shipped', NULL);

    INSERT INTO legacy.ORD_HDR (ORD_ID, CUST_ID, ORD_DT, ORD_AMT, ORD_STAT, LAST_SYNCED_FROM)
    VALUES ('ORD-001', 'C001', CURRENT_DATE, 999.99, 'S', NULL);
END;
$$ LANGUAGE plpgsql;
