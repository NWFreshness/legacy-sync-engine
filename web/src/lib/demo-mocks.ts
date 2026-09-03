/**
 * Demo fixtures — used when the FastAPI backend isn't running.
 * Shapes match `types.ts` exactly. Data matches `seed_dirty_data()` in
 * docs/data_model.sql so the UI demos correctly even before @backend lands.
 */

import type {
  AuditEntry,
  Conflict,
  HealthResponse,
  MappingSummary,
  ResolveConflictRequest,
  SyncTriggerResponse,
} from "../types";

const now = Date.now();
const ago = (ms: number) => new Date(now - ms).toISOString();

export const DEMO_CONFLICTS: Conflict[] = [
  {
    conflict_id: "cf-0001-0000-0000-0000-000000000001",
    table_name: "customers",
    record_id: "C001",
    detected_at: ago(45_000),
    strategy: "manual_review",
    status: "pending",
    modern_state: {
      id: "00000000-0000-0000-0000-000000000001",
      email: "alice@acme.com",
      name: "Alice Smith",
      company: "Acme Corp",
      status: "active",
      updated_at: ago(3600 * 1000),
      version: 2,
    },
    legacy_state: {
      CUST_ID: "C001",
      CUST_NAME: "Alice Smith-Updated",
      CUST_EMAIL: "alice.new@acme.com",
      CUST_CO: "ACME INC",
      STATUS_CD: "A",
      LAST_UPD_DT: "2026-09-02",
      VERSION_NO: 3,
    },
    proposed_resolutions: [
      { source: "modern", label: "Keep modern (source of truth)" },
      { source: "legacy", label: "Keep legacy ERP values" },
      { source: "merge", label: "Merge (email from modern, name from legacy)" },
    ],
  },
  {
    conflict_id: "cf-0001-0000-0000-0000-000000000002",
    table_name: "orders",
    record_id: "ORD-001",
    detected_at: ago(180_000),
    strategy: "manual_review",
    status: "pending",
    modern_state: {
      id: "00000000-0000-0000-0000-000000000002",
      customer_id: "00000000-0000-0000-0000-000000000001",
      order_date: ago(7200 * 1000),
      total_amount: "1250.00",
      status: "shipped",
      version: 3,
    },
    legacy_state: {
      ORD_ID: "ORD-001",
      CUST_ID: "C001",
      ORD_DT: "2026-09-03",
      ORD_AMT: "999.99",
      ORD_STAT: "S",
      VERSION_NO: 2,
    },
    proposed_resolutions: [
      { source: "modern", label: "Keep modern (shipped, $1250)" },
      { source: "legacy", label: "Keep legacy (shipped, $999.99)" },
    ],
  },
  {
    conflict_id: "cf-0001-0000-0000-0000-000000000003",
    table_name: "inventory",
    record_id: "WIDGET-01",
    detected_at: ago(9_000),
    strategy: "manual_review",
    status: "pending",
    modern_state: {
      product_id: "WIDGET-01",
      quantity: 47,
      last_restock: ago(86_400 * 3 * 1000),
      version: 5,
    },
    legacy_state: {
      PROD_CD: "WIDGET-01",
      QTY_OH: 12,
      LAST_REPL_DT: "2026-09-01",
      VERSION_NO: 4,
    },
    proposed_resolutions: [
      { source: "modern", label: "Trust modern (47 on hand, recent restock)" },
      { source: "legacy", label: "Trust legacy ERP (12 on hand)" },
    ],
  },
];

export const DEMO_AUDIT: AuditEntry[] = [
  {
    event_id: "ev-0001-0000-0000-0000-000000000001",
    created_at: ago(5_000),
    direction: "modern_to_legacy",
    table_name: "customers",
    record_id: "C001",
    resolution_strategy: "last_write_wins",
    status: "applied",
    before: { name: "Alice Smith", email: "alice@acme.com" },
    after: { name: "Alice Smith", email: "alice@acme.com", company: "Acme Corp" },
  },
  {
    event_id: "ev-0001-0000-0000-0000-000000000002",
    created_at: ago(45_000),
    direction: "legacy_to_modern",
    table_name: "customers",
    record_id: "C001",
    resolution_strategy: "manual_review",
    status: "failed",
    before: { name: "Alice Smith", email: "alice@acme.com" },
    after: { name: "Alice Smith-Updated", email: "alice.new@acme.com" },
  },
  {
    event_id: "ev-0001-0000-0000-0000-000000000003",
    created_at: ago(120_000),
    direction: "modern_to_legacy",
    table_name: "inventory",
    record_id: "WIDGET-01",
    resolution_strategy: "source_of_truth_wins",
    status: "applied",
    before: { QTY_OH: 12 },
    after: { QTY_OH: 47 },
  },
  {
    event_id: "ev-0001-0000-0000-0000-000000000004",
    created_at: ago(300_000),
    direction: "legacy_to_modern",
    table_name: "orders",
    record_id: "ORD-001",
    resolution_strategy: "last_write_wins",
    status: "applied",
    before: { total_amount: "1250.00", status: "shipped" },
    after: { total_amount: "1250.00", status: "shipped" },
  },
  {
    event_id: "ev-0001-0000-0000-0000-000000000005",
    created_at: ago(600_000),
    direction: "modern_to_legacy",
    table_name: "orders",
    record_id: "ORD-001",
    resolution_strategy: "last_write_wins",
    status: "dead_lettered",
    before: null,
    after: { total_amount: "BAD-VALUE" },
  },
];

export const DEMO_MAPPINGS: MappingSummary[] = [
  {
    version: "1.0",
    table: "customers",
    source_of_truth: "modern",
    conflict_strategy: "last_write_wins",
    clock_skew_tolerance_seconds: 5,
  },
  {
    version: "1.0",
    table: "orders",
    source_of_truth: "both",
    conflict_strategy: "manual_review",
    clock_skew_tolerance_seconds: 5,
  },
  {
    version: "1.0",
    table: "inventory",
    source_of_truth: "modern",
    conflict_strategy: "source_of_truth_wins",
    clock_skew_tolerance_seconds: 5,
  },
];

export const DEMO_HEALTH: HealthResponse = {
  status: "demo-mode",
  modern_db: "up",
  legacy_db: "up",
  last_sync: {
    customers_modern_to_legacy: ago(5_000),
    customers_legacy_to_modern: ago(45_000),
    inventory_modern_to_legacy: ago(120_000),
    orders_legacy_to_modern: ago(300_000),
  },
  mapping_versions: ["1.0/customers", "1.0/orders", "1.0/inventory"],
};

// In-memory mutable copy so resolve actions feel real in demo mode.
let liveConflicts: Conflict[] = DEMO_CONFLICTS.map((c) => ({ ...c }));
let liveAudit: AuditEntry[] = [...DEMO_AUDIT];

export const mocks = {
  health: async (): Promise<HealthResponse> => DEMO_HEALTH,
  listConflicts: async (): Promise<Conflict[]> =>
    liveConflicts.filter((c) => c.status === "pending"),
  listResolvedConflicts: async (): Promise<Conflict[]> =>
    liveConflicts.filter((c) => c.status === "resolved"),
  listAudit: async (limit = 20): Promise<AuditEntry[]> =>
    liveAudit.slice(0, limit),
  listMappings: async (): Promise<MappingSummary[]> => DEMO_MAPPINGS,
  triggerSync: async (
    table: string,
    direction: string,
  ): Promise<SyncTriggerResponse> => {
    const id = `job-${Math.random().toString(16).slice(2, 10)}`;
    return {
      job_id: id,
      status: "queued",
      message: `sync queued for ${table} (${direction}) — backend would run poller + mapping engine`,
    };
  },
  resolveConflict: async (
    req: Omit<ResolveConflictRequest, "conflict_id"> & { conflict_id: string },
  ): Promise<{ status: string }> => {
    const idx = liveConflicts.findIndex(
      (c) => c.conflict_id === req.conflict_id,
    );
    if (idx === -1) {
      throw new Error(`conflict ${req.conflict_id} not found`);
    }
    const c = liveConflicts[idx]!;
    liveConflicts[idx] = {
      ...c,
      status: "resolved",
    };
    liveAudit = [
      {
        event_id: `ev-${Math.random().toString(16).slice(2, 10)}`,
        created_at: new Date().toISOString(),
        direction: "modern_to_legacy",
        table_name: c.table_name,
        record_id: c.record_id,
        resolution_strategy: "manual_review",
        status: "applied",
        before: c.legacy_state,
        after: req.chosen_state,
      },
      ...liveAudit,
    ];
    return { status: "resolved" };
  },
};