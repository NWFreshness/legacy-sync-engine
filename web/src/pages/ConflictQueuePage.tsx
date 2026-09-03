import { useCallback, useEffect, useState } from "react";
import ConflictCard from "../components/ConflictCard";
import { data } from "../lib/data";
import type { Conflict } from "../types";

interface Props {
  resolved?: boolean;
}

export default function ConflictQueuePage({ resolved = false }: Props) {
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const list = resolved
        ? await data.listResolvedConflicts()
        : await data.listPendingConflicts();
      setConflicts(list);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      setConflicts([]);
    }
  }, [resolved]);

  useEffect(() => {
    reload();
    if (resolved) return;
    const id = setInterval(reload, 4000);
    return () => clearInterval(id);
  }, [reload, resolved]);

  async function handleResolve(
    conflictId: string,
    _choiceIdx: number,
    notes: string,
  ) {
    setResolving(conflictId);
    try {
      const conflict = conflicts?.find((c) => c.conflict_id === conflictId);
      if (!conflict) throw new Error("conflict vanished");
      const proposal = conflict.proposed_resolutions[_choiceIdx];
      const chosenState =
        proposal?.["source"] === "legacy" ? conflict.legacy_state : conflict.modern_state;
      await data.resolveConflict({
        conflict_id: conflictId,
        chosen_state: chosenState as Record<string, unknown>,
        resolution_notes: notes || null,
        resolved_by: "operator@local",
      });
      // Optimistic update + refresh
      setConflicts((prev) =>
        prev?.filter((c) => c.conflict_id !== conflictId) ?? prev,
      );
      await reload();
    } finally {
      setResolving(null);
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-eyebrow">
            {resolved ? "history · resolved" : "live · pending"}
          </div>
          <h1 className="page-title">
            {resolved ? "resolved conflicts" : "conflict queue"}
          </h1>
          <p className="page-sub">
            {resolved
              ? "Manual review resolutions applied to either modern or legacy stores. Audited."
              : "Bi-directional sync stopped because both sides changed within the clock-skew tolerance. Pick a resolution to unblock the queue."}
          </p>
        </div>
        {!resolved && (
          <button className="btn btn-ghost" onClick={reload} aria-label="refresh">
            ↻ refresh
          </button>
        )}
      </div>

      {error && (
        <div className="error-strip" role="alert">
          <span className="label">load failed</span>
          {error}
        </div>
      )}

      {!conflicts && (
        <div className="state-box" aria-live="polite">
          <div className="glyph">…</div>
          <div className="title">loading queue</div>
          <div className="body">Fetching from /api/conflicts…</div>
        </div>
      )}

      {conflicts && conflicts.length === 0 && (
        <div className="state-box" aria-live="polite">
          <div className="glyph">∅</div>
          <div className="title">
            {resolved ? "no resolutions yet" : "queue is clear"}
          </div>
          <div className="body">
            {resolved
              ? "Once operators resolve conflicts, they'll appear here with audit metadata."
              : "No two-system writes happened within the skew window. Trigger a sync to seed a conflict."}
          </div>
        </div>
      )}

      {conflicts && conflicts.length > 0 && (
        <>
          <div className="stat-strip" aria-label="conflict summary">
            <div className="stat alert">
              <div className="label">pending</div>
              <div className="value">{conflicts.length}</div>
              <div className="delta">manual review strategy</div>
            </div>
            <div className="stat">
              <div className="label">tables affected</div>
              <div className="value">
                {new Set(conflicts.map((c) => c.table_name)).size}
              </div>
              <div className="delta">distinct record sets</div>
            </div>
            <div className="stat">
              <div className="label">oldest</div>
              <div className="value" style={{ fontSize: "var(--fs-md)" }}>
                {conflicts.length > 0
                  ? formatAgeOfOldest(conflicts)
                  : "—"}
              </div>
              <div className="delta">since detection</div>
            </div>
          </div>

          <div className="conflict-list">
            {conflicts.map((c) => (
              <ConflictCard
                key={c.conflict_id}
                conflict={c}
                selected={selectedId === c.conflict_id || resolving === c.conflict_id}
                onSelect={setSelectedId}
                onResolve={handleResolve}
                resolved={resolved}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function formatAgeOfOldest(conflicts: Conflict[]): string {
  const oldest = conflicts.reduce((a, b) =>
    new Date(a.detected_at).getTime() < new Date(b.detected_at).getTime() ? a : b,
  );
  const ageMs = Date.now() - new Date(oldest.detected_at).getTime();
  if (ageMs < 60_000) return `${Math.round(ageMs / 1000)}s`;
  if (ageMs < 3_600_000) return `${Math.round(ageMs / 60_000)}m`;
  return `${Math.round(ageMs / 3_600_000)}h`;
}