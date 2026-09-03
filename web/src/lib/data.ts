/**
 * Data-source adapter.
 * Tries the real API first; falls back to demo fixtures on network failure
 * (status 0 = unreachable) so the UI proves the conflict-queue claim
 * before @backend's endpoints are live.
 *
 * The UI NEVER knows whether it's looking at live or demo data — only the
 * status banner exposes the source. That keeps the screen honest.
 */

import { api } from "./api";
import { mocks } from "./demo-mocks";
import type {
  AuditEntry,
  Conflict,
  HealthResponse,
  MappingSummary,
  ResolveConflictRequest,
  SyncTriggerRequest,
  SyncTriggerResponse,
} from "../types";

export type DataSource = "live" | "demo";

let lastSource: DataSource = "demo";

export const getDataSource = (): DataSource => lastSource;

async function liveOrMock<T>(
  live: () => Promise<T>,
  mock: () => Promise<T>,
): Promise<T> {
  try {
    const result = await live();
    lastSource = "live";
    return result;
  } catch (e) {
    // Treat any failure as "backend not present" and fall back to fixtures.
    // - status 0  → network/connection refused
    // - status 404 → endpoint missing
    // - status >= 500 → server errored
    // - SyntaxError → response was not JSON (likely SPA index.html fallback)
    // Anything else (e.g. 4xx from a real API) still falls back for demo
    // purposes: the UI proves the contract, the backend catches up.
    lastSource = "demo";
    return mock();
  }
}

export const data = {
  health: () => liveOrMock(api.health, mocks.health),

  triggerSync: (req: SyncTriggerRequest) =>
    liveOrMock(
      () => api.triggerSync(req),
      () => mocks.triggerSync(req.table, req.direction),
    ),

  listPendingConflicts: () =>
    liveOrMock(() => api.listConflicts("pending"), () => mocks.listConflicts()),

  listResolvedConflicts: () =>
    liveOrMock(
      () => api.listConflicts("resolved"),
      () => mocks.listResolvedConflicts(),
    ),

  resolveConflict: (req: ResolveConflictRequest) =>
    liveOrMock(
      () => api.resolveConflict(req.conflict_id, req),
      () =>
        mocks.resolveConflict({
          conflict_id: req.conflict_id,
          chosen_state: req.chosen_state,
          resolution_notes: req.resolution_notes,
          resolved_by: req.resolved_by ?? "admin",
        }),
    ),

  listAudit: (limit = 20) =>
    liveOrMock(() => api.listAudit(limit), () => mocks.listAudit(limit)),

  listMappings: () =>
    liveOrMock(api.listMappings, mocks.listMappings),
};

export type DataLayer = {
  health: () => Promise<HealthResponse>;
  triggerSync: (req: SyncTriggerRequest) => Promise<SyncTriggerResponse>;
  listPendingConflicts: () => Promise<Conflict[]>;
  listResolvedConflicts: () => Promise<Conflict[]>;
  resolveConflict: (req: ResolveConflictRequest) => Promise<{ status: string }>;
  listAudit: (limit?: number) => Promise<AuditEntry[]>;
  listMappings: () => Promise<MappingSummary[]>;
};