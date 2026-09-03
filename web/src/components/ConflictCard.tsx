import { useMemo, useState } from "react";
import type { Conflict } from "../types";
import { StatusPill, formatRelativeTime, formatAbsoluteTime } from "./primitives";

interface ConflictCardProps {
  conflict: Conflict;
  selected?: boolean;
  onSelect?: (id: string) => void;
  onResolve: (conflictId: string, choice: number, notes: string) => Promise<void>;
  resolved?: boolean;
}

export default function ConflictCard({
  conflict,
  selected,
  onSelect,
  onResolve,
  resolved,
}: ConflictCardProps) {
  const [chosenIdx, setChosenIdx] = useState<number>(0);
  const [notes, setNotes] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const diff = useMemo(() => {
    return diffRecords(
      conflict.modern_state as Record<string, unknown>,
      conflict.legacy_state as Record<string, unknown>,
    );
  }, [conflict]);

  async function handleResolve() {
    setBusy(true);
    setError(null);
    try {
      await onResolve(conflict.conflict_id, chosenIdx, notes);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      className={`conflict-card${selected ? " selected" : ""}`}
      aria-labelledby={`conflict-${conflict.conflict_id}-head`}
    >
      <header className="conflict-card-head">
        <div>
          <div className="conflict-record">
            <span className="table">{conflict.table_name}</span>
            <span className="id">{conflict.record_id}</span>
            <StatusPill status={conflict.status} />
          </div>
          <div
            id={`conflict-${conflict.conflict_id}-head`}
            className="conflict-id"
            style={{ marginTop: 4 }}
          >
            conflict {conflict.conflict_id.slice(0, 18)}…
            <span style={{ color: "var(--fg-dim)" }}> · </span>
            detected {formatRelativeTime(conflict.detected_at)}{" "}
            <span title={formatAbsoluteTime(conflict.detected_at)} aria-label="absolute time">
              ({formatAbsoluteTime(conflict.detected_at)})
            </span>
          </div>
        </div>
        {!resolved && onSelect && (
          <button
            className="btn btn-ghost"
            onClick={() => onSelect(conflict.conflict_id)}
            aria-pressed={selected}
          >
            {selected ? "selected" : "focus"}
          </button>
        )}
      </header>

      <div className="conflict-card-body">
        <section className="side modern" aria-label="modern database state">
          <div className="side-head">
            <span className="marker" aria-hidden />
            modern · modern_saas
          </div>
          <dl className="field-list">
            {Object.entries(conflict.modern_state).map(([k, v]) => {
              const isDiff = diff.modernChanged.has(k);
              return (
                <div
                  key={k}
                  className={`field-row modern${isDiff ? " diff" : ""}`}
                >
                  <dt className="key">{k}</dt>
                  <dd className="value">{formatValue(v)}</dd>
                  <dd aria-hidden />
                </div>
              );
            })}
          </dl>
        </section>

        <section className="side legacy" aria-label="legacy ERP state">
          <div className="side-head">
            <span className="marker" aria-hidden />
            legacy · legacy_erp (legacy naming)
          </div>
          <dl className="field-list">
            {Object.entries(conflict.legacy_state).map(([k, v]) => {
              const isDiff = diff.legacyChanged.has(k);
              return (
                <div
                  key={k}
                  className={`field-row legacy${isDiff ? " diff" : ""}`}
                >
                  <dt className="key">{k}</dt>
                  <dd className="value">{formatValue(v)}</dd>
                  <dd aria-hidden />
                </div>
              );
            })}
          </dl>
        </section>
      </div>

      {!resolved && (
        <footer className="conflict-card-actions" aria-label="resolution actions">
          <div
            className="resolution-options"
            role="radiogroup"
            aria-label={`resolution choice for ${conflict.record_id}`}
          >
            {conflict.proposed_resolutions.map((p, i) => (
              <button
                key={i}
                className={`resolution-chip${chosenIdx === i ? " selected" : ""}`}
                role="radio"
                aria-checked={chosenIdx === i}
                onClick={() => setChosenIdx(i)}
                type="button"
              >
                <span className="chip-tag">
                  {String(p["source"] ?? "?")}
                </span>
                <span>{String(p["label"] ?? `option ${i + 1}`)}</span>
              </button>
            ))}
          </div>
          <input
            type="text"
            placeholder="notes (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="resolution-notes"
            aria-label="resolution notes"
            style={{
              flex: "0 1 200px",
              background: "var(--bg-base)",
              border: "1px solid var(--border-base)",
              color: "var(--fg-primary)",
              borderRadius: "var(--r-sm)",
              padding: "var(--sp-2) var(--sp-3)",
              fontSize: "var(--fs-sm)",
            }}
          />
          <button
            className="btn btn-primary"
            onClick={handleResolve}
            disabled={busy}
            aria-busy={busy}
          >
            {busy ? "applying…" : "apply resolution"}
          </button>
        </footer>
      )}

      {error && (
        <div className="error-strip" role="alert" style={{ margin: "var(--sp-3) var(--sp-5)" }}>
          <span className="label">resolve failed</span>
          {error}
        </div>
      )}
    </article>
  );
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/**
 * Field-level diff: for each top-level key in modern_state, mark changed
 * if (a) key absent in legacy, (b) value != deep-equal, OR (c) legacy has a
 * sibling key with a different value. This is intentionally simple — the
 * SyncEngine would do real column-mapping diffs server-side.
 *
 * Bookkeeping columns (version, timestamps) are excluded so the diff
 * surfaces real content disagreements, not internal counters.
 */
function diffRecords(
  modern: Record<string, unknown>,
  legacy: Record<string, unknown>,
): { modernChanged: Set<string>; legacyChanged: Set<string> } {
  const modernChanged = new Set<string>();
  const legacyChanged = new Set<string>();

  for (const [k, v] of Object.entries(modern)) {
    if (isBookkeeping(k)) continue;
    const matched = findLegacyKey(k, legacy);
    if (matched === null) {
      modernChanged.add(k);
      continue;
    }
    if (isBookkeeping(matched)) continue;
    if (!deepEqual(v, legacy[matched])) {
      modernChanged.add(k);
      legacyChanged.add(matched);
    }
  }
  for (const [k] of Object.entries(legacy)) {
    if (isBookkeeping(k)) continue;
    if (!findModernKey(k, modern)) {
      legacyChanged.add(k);
    }
  }
  return { modernChanged, legacyChanged };
}

const BOOKKEEPING_KEYS = new Set([
  "version",
  "VERSION_NO",
  "updated_at",
  "LAST_UPD_DT",
  "last_restock",
  "LAST_REPL_DT",
  "id",
  "CUST_ID",
  "ORD_ID",
  "PROD_CD",
]);

function isBookkeeping(k: string): boolean {
  return BOOKKEEPING_KEYS.has(k);
}

const FIELD_ALIASES: Record<string, string> = {
  id: "CUST_ID",
  email: "CUST_EMAIL",
  name: "CUST_NAME",
  company: "CUST_CO",
  status: "STATUS_CD",
  updated_at: "LAST_UPD_DT",
  order_date: "ORD_DT",
  total_amount: "ORD_AMT",
  quantity: "QTY_OH",
  last_restock: "LAST_REPL_DT",
};

function findLegacyKey(modernKey: string, legacy: Record<string, unknown>): string | null {
  if (legacy[modernKey] !== undefined) return modernKey;
  const alias = FIELD_ALIASES[modernKey];
  if (alias && legacy[alias] !== undefined) return alias;
  // Loose: case-insensitive contains
  for (const k of Object.keys(legacy)) {
    if (k.toLowerCase() === modernKey.toLowerCase()) return k;
  }
  return null;
}

function findModernKey(legacyKey: string, modern: Record<string, unknown>): string | null {
  if (modern[legacyKey] !== undefined) return legacyKey;
  for (const [m, l] of Object.entries(FIELD_ALIASES)) {
    if (l === legacyKey && modern[m] !== undefined) return m;
  }
  for (const k of Object.keys(modern)) {
    if (k.toLowerCase() === legacyKey.toLowerCase()) return k;
  }
  return null;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return false;
  if (typeof a === "object") return JSON.stringify(a) === JSON.stringify(b);
  return false;
}