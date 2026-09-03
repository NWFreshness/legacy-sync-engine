/**
 * Type mirrors of @architect's api_contract.md (legacy-sync-engine v1).
 * Field names match the Pydantic schemas verbatim. If the contract changes,
 * this file changes too — no parallel visual language for "similar" fields.
 */

export type SyncDirection =
  | "modern_to_legacy"
  | "legacy_to_modern";

export type ResolutionStrategy =
  | "last_write_wins"
  | "source_of_truth_wins"
  | "manual_review";

export type SyncStatus =
  | "pending"
  | "applied"
  | "failed"
  | "dead_lettered";

export interface MappingField {
  modern_field: string;
  legacy_field: string;
  type: "string" | "integer" | "datetime" | "numeric";
  direction: "both" | "modern_to_legacy" | "legacy_to_modern";
  required?: boolean;
  transform?: Record<string, string> | null;
  default?: unknown;
}

export interface TableMapping {
  version: string;
  table: string;
  source_of_truth: "modern" | "legacy" | "both";
  conflict_strategy: ResolutionStrategy;
  clock_skew_tolerance_seconds: number;
  fields: MappingField[];
}

export interface Conflict {
  conflict_id: string;
  table_name: string;
  record_id: string;
  modern_state: Record<string, unknown>;
  legacy_state: Record<string, unknown>;
  detected_at: string;
  strategy: ResolutionStrategy;
  status: "pending" | "resolved";
  proposed_resolutions: Array<Record<string, unknown>>;
}

export interface ResolveConflictRequest {
  conflict_id: string;
  chosen_state: Record<string, unknown>;
  resolution_notes?: string | null;
  resolved_by?: string;
}

export interface SyncTriggerRequest {
  table: "customers" | "orders" | "inventory";
  direction: SyncDirection | "both";
}

export interface SyncTriggerResponse {
  job_id: string;
  status: string;
  message: string;
}

export interface AuditEntry {
  event_id: string;
  created_at: string;
  direction: SyncDirection;
  table_name: string;
  record_id: string;
  resolution_strategy: ResolutionStrategy;
  status: SyncStatus;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface HealthResponse {
  status: string;
  modern_db: "up" | "down";
  legacy_db: "up" | "down";
  last_sync: Record<string, string | null>;
  mapping_versions: string[];
}

export interface MappingSummary {
  version: string;
  table: string;
  source_of_truth: string;
  conflict_strategy: ResolutionStrategy;
  clock_skew_tolerance_seconds: number;
}