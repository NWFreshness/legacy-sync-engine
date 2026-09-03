-- legacy-sync-engine/db/seeds/001_dirty_data.sql
-- Overlapping dirty seed (idempotent). The demo's first conflict comes from
-- the alice/C001 row pair: same person, drifted on both sides.

BEGIN;

-- ---- modern side -----------------------------------------------------------
INSERT INTO modern.customers (id, email, name, company, status, updated_at, last_synced_from)
VALUES
  ('00000000-0000-0000-0000-000000000001'::uuid, 'alice@acme.com', 'Alice Smith', 'Acme Corp', 'active', NOW() - INTERVAL '1 hour', NULL),
  ('00000000-0000-0000-0000-000000000003'::uuid, 'bob@initech.com', 'Bob Porter', 'Initech', 'active', NOW() - INTERVAL '2 hours', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO modern.orders (id, customer_id, order_date, total_amount, status, last_synced_from)
VALUES
  ('00000000-0000-0000-0000-000000000002'::uuid, '00000000-0000-0000-0000-000000000001'::uuid, NOW() - INTERVAL '1 day', 1250.00, 'shipped', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO modern.inventory (product_id, quantity, last_restock, last_synced_from)
VALUES
  ('WIDGET-1', 150, NOW() - INTERVAL '3 days', NULL),
  ('GADGET-9', 0,  NULL, NULL)
ON CONFLICT (product_id) DO NOTHING;

-- ---- legacy side (drifted twins + legacy-only rows) ------------------------
INSERT INTO legacy.CUST_MSTR (CUST_ID, CUST_NAME, CUST_EMAIL, CUST_CO, STATUS_CD, LAST_UPD_DT, LAST_SYNCED_FROM)
VALUES
  ('C001', 'Alice Smith Updated', 'alice.new@acme.com', 'ACME INC', 'A', CURRENT_DATE - 1, NULL),
  ('C002', 'Carol Danvers', 'carol@hal.com', 'HAL Enterprises', 'I', NULL, NULL)
ON CONFLICT (CUST_ID) DO NOTHING;

INSERT INTO legacy.ORD_HDR (ORD_ID, CUST_ID, ORD_DT, ORD_AMT, ORD_STAT, LAST_SYNCED_FROM)
VALUES
  ('ORD-001', 'C001', CURRENT_DATE, 999.99, 'S', NULL)
ON CONFLICT (ORD_ID) DO NOTHING;

INSERT INTO legacy.INV_BAL (PROD_CD, QTY_OH, LAST_REPL_DT, LAST_SYNCED_FROM)
VALUES
  ('WIDGET-1', 175, CURRENT_DATE - 2, NULL)
ON CONFLICT (PROD_CD) DO NOTHING;

-- Poison row for the demo: required email is NULL -> mapping error -> DLQ.
INSERT INTO legacy.CUST_MSTR (CUST_ID, CUST_NAME, CUST_EMAIL, CUST_CO, STATUS_CD, LAST_UPD_DT, LAST_SYNCED_FROM)
VALUES ('C666', 'Bad Data Barry', NULL, 'Nowhere', 'A', NULL, NULL)
ON CONFLICT (CUST_ID) DO NOTHING;

-- Make the legacy changes observable to the CDC poller.
INSERT INTO legacy.CHANGE_LOG (table_name, record_id, operation, payload)
VALUES
  ('CUST_MSTR', 'C001', 'U', '{"CUST_ID":"C001","note":"seeded drift"}'::jsonb),
  ('CUST_MSTR', 'C002', 'I', '{"CUST_ID":"C002"}'::jsonb),
  ('CUST_MSTR', 'C666', 'I', '{"CUST_ID":"C666"}'::jsonb);

COMMIT;
