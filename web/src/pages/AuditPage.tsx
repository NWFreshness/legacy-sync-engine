import { useEffect, useState } from "react";
import { data } from "../lib/data";
import type { AuditEntry } from "../types";
import { StatusPill, formatRelativeTime, formatAbsoluteTime } from "../components/primitives";

const TABLE_FILTERS = ["all", "customers", "orders", "inventory"] as const;
type TableFilter = (typeof TABLE_FILTERS)[number];

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [filter, setFilter] = useState<TableFilter>("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const all = await data.listAudit(50);
        if (cancelled) return;
        setEntries(
          filter === "all" ? all : all.filter((e) => e.table_name === filter),
        );
        setError(null);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    }
    load();
    const id = setInterval(load, 6000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [filter]);

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-eyebrow">history · immutable</div>
          <h1 className="page-title">audit log</h1>
          <p className="page-sub">
            Every sync event recorded with before/after payloads, mapping version, and resolution strategy.
            Used to answer &ldquo;who changed this row, and from which side&rdquo;.
          </p>
        </div>
        <div className="chip-row" role="tablist" aria-label="table filter">
          {TABLE_FILTERS.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={filter === t}
              className={`chip${filter === t ? " active" : ""}`}
              onClick={() => setFilter(t)}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="error-strip" role="alert">
          <span className="label">load failed</span>
          {error}
        </div>
      )}

      {entries.length === 0 ? (
        <div className="state-box">
          <div className="glyph">∅</div>
          <div className="title">no audit entries</div>
          <div className="body">
            Trigger a sync to populate this log. Every event is recorded with before/after payloads.
          </div>
        </div>
      ) : (
        <table className="data-table" aria-label="audit log">
          <thead>
            <tr>
              <th scope="col">event</th>
              <th scope="col">table</th>
              <th scope="col">record</th>
              <th scope="col">direction</th>
              <th scope="col">strategy</th>
              <th scope="col">status</th>
              <th scope="col">when</th>
              <th scope="col">payload</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.event_id}>
                <td>
                  <span className="mono">{e.event_id.slice(0, 14)}…</span>
                </td>
                <td>
                  <span className="mono">{e.table_name}</span>
                </td>
                <td>
                  <span className="id">{e.record_id}</span>
                </td>
                <td>
                  <span
                    className="mono"
                    style={{
                      color:
                        e.direction === "legacy_to_modern"
                          ? "var(--fg-legacy)"
                          : "var(--signal-info)",
                    }}
                  >
                    {e.direction === "legacy_to_modern" ? "legacy → modern" : "modern → legacy"}
                  </span>
                </td>
                <td>
                  <span className="mono">{e.resolution_strategy}</span>
                </td>
                <td>
                  <StatusPill status={e.status} />
                </td>
                <td>
                  <span className="mono" title={formatAbsoluteTime(e.created_at)}>
                    {formatRelativeTime(e.created_at)}
                  </span>
                </td>
                <td>
                  <details>
                    <summary
                      style={{
                        cursor: "pointer",
                        color: "var(--fg-muted)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--fs-xs)",
                      }}
                    >
                      before / after
                    </summary>
                    <pre
                      style={{
                        margin: "var(--sp-2) 0 0",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--fs-xs)",
                        color: "var(--fg-secondary)",
                        background: "var(--bg-inset)",
                        padding: "var(--sp-2) var(--sp-3)",
                        borderRadius: "var(--r-xs)",
                        overflow: "auto",
                        maxWidth: "420px",
                      }}
                    >
                      before: {JSON.stringify(e.before, null, 0)}
                      {"\n"}after:  {JSON.stringify(e.after, null, 0)}
                    </pre>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}