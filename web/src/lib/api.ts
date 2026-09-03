import type {
  AuditEntry,
  Conflict,
  HealthResponse,
  MappingSummary,
  ResolveConflictRequest,
  SyncTriggerRequest,
  SyncTriggerResponse,
} from "../types";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (e) {
    // Network failure (likely backend not up in dev/demo).
    // Surface a distinct error so the UI can fall back to mocks gracefully.
    throw new ApiError(0, null, `network unreachable: ${(e as Error).message}`);
  }
  if (!res.ok) {
    let body: unknown = null;
    try { body = await res.json(); } catch { /* ignore */ }
    throw new ApiError(res.status, body, `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  triggerSync: (body: SyncTriggerRequest) =>
    request<SyncTriggerResponse>("/api/sync/trigger", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listConflicts: (status: "pending" | "resolved" = "pending") =>
    request<Conflict[]>(`/api/conflicts?status=${status}`),

  resolveConflict: (conflictId: string, body: Omit<ResolveConflictRequest, "conflict_id">) =>
    request<{ status: string }>(`/api/conflicts/${conflictId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ ...body, conflict_id: conflictId }),
    }),

  listAudit: (limit = 20, table?: string) =>
    request<AuditEntry[]>(
      `/api/audit?limit=${limit}${table ? `&table=${table}` : ""}`,
    ),

  listMappings: () =>
    request<MappingSummary[]>("/api/mappings"),
};