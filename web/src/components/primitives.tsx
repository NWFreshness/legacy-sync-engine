import type { SyncStatus } from "../types";

export function StatusPill({ status }: { status: SyncStatus | "pending" | "resolved" }) {
  return (
    <span className={`pill ${status}`} role="status" aria-label={`status ${status}`}>
      <span className="dot" aria-hidden />
      {status.replace("_", " ")}
    </span>
  );
}

export function formatRelativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const delta = Date.now() - t;
  if (delta < 0) {
    const ahead = Math.abs(delta);
    if (ahead < 60_000) return `in ${Math.round(ahead / 1000)}s`;
    if (ahead < 3_600_000) return `in ${Math.round(ahead / 60_000)}m`;
    return `in ${Math.round(ahead / 3_600_000)}h`;
  }
  if (delta < 60_000) return `${Math.round(delta / 1000)}s ago`;
  if (delta < 3_600_000) return `${Math.round(delta / 60_000)}m ago`;
  if (delta < 86_400_000) return `${Math.round(delta / 3_600_000)}h ago`;
  return `${Math.round(delta / 86_400_000)}d ago`;
}

export function formatAbsoluteTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}