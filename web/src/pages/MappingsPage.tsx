import { useEffect, useState } from "react";
import { data } from "../lib/data";
import type { MappingSummary } from "../types";

export default function MappingsPage() {
  const [mappings, setMappings] = useState<MappingSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const list = await data.listMappings();
        if (!cancelled) setMappings(list);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-eyebrow">config · versioned yaml</div>
          <h1 className="page-title">field mappings</h1>
          <p className="page-sub">
            Versioned mapping YAMLs loaded at engine startup. Each table pins a source-of-truth side, a
            conflict strategy, and a clock-skew tolerance. Mapping drift between YAML and DB is caught by
            contract tests on boot.
          </p>
        </div>
      </div>

      {error && (
        <div className="error-strip" role="alert">
          <span className="label">load failed</span>
          {error}
        </div>
      )}

      {!mappings && (
        <div className="state-box">
          <div className="glyph">…</div>
          <div className="title">loading mappings</div>
        </div>
      )}

      {mappings && mappings.length === 0 && (
        <div className="state-box">
          <div className="glyph">∅</div>
          <div className="title">no mappings registered</div>
        </div>
      )}

      {mappings && mappings.length > 0 && (
        <div>
          {mappings.map((m) => (
            <article className="mapping-card" key={`${m.version}-${m.table}`}>
              <header className="mapping-card-head">
                <span className="ver">v{m.version}</span>
                <h2 className="table-name">{m.table}</h2>
                <span
                  className="pill"
                  style={{
                    color:
                      m.source_of_truth === "modern"
                        ? "var(--signal-info)"
                        : m.source_of_truth === "legacy"
                          ? "var(--fg-legacy)"
                          : "var(--fg-secondary)",
                    borderColor:
                      m.source_of_truth === "modern"
                        ? "var(--signal-info-dim)"
                        : m.source_of_truth === "legacy"
                          ? "var(--border-legacy)"
                          : "var(--border-base)",
                    background:
                      m.source_of_truth === "modern"
                        ? "var(--signal-info-dim)"
                        : m.source_of_truth === "legacy"
                          ? "var(--bg-legacy-hi)"
                          : "var(--bg-elev-2)",
                  }}
                >
                  source-of-truth: {m.source_of_truth}
                </span>
                <span
                  className="pill"
                  style={{
                    color:
                      m.conflict_strategy === "manual_review"
                        ? "var(--signal-conflict)"
                        : "var(--accent-primary)",
                    borderColor:
                      m.conflict_strategy === "manual_review"
                        ? "var(--signal-conflict-dim)"
                        : "var(--accent-primary-dim)",
                    background:
                      m.conflict_strategy === "manual_review"
                        ? "var(--signal-conflict-dim)"
                        : "var(--accent-primary-dim)",
                  }}
                >
                  strategy: {m.conflict_strategy}
                </span>
                <span className="pill" style={{ color: "var(--fg-muted)", borderColor: "var(--border-faint)", background: "transparent" }}>
                  skew ±{m.clock_skew_tolerance_seconds}s
                </span>
              </header>

              <div className="field-table" role="table" aria-label={`fields for ${m.table}`}>
                <div className="head" role="columnheader">modern field</div>
                <div className="head" role="columnheader">legacy field</div>
                <div className="head" role="columnheader">type</div>
                <div className="head" role="columnheader">direction</div>

                {sampleFieldsForTable(m.table).map((f, i) => (
                  <FieldRow
                    key={`${f.modern}-${i}`}
                    modern={f.modern}
                    legacy={f.legacy}
                    type={f.type}
                    direction={f.direction}
                  />
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

function FieldRow({
  modern,
  legacy,
  type,
  direction,
}: {
  modern: string;
  legacy: string;
  type: string;
  direction: string;
}) {
  return (
    <>
      <div className="row modern">{modern}</div>
      <div className="row legacy">{legacy}</div>
      <div className="row">{type}</div>
      <div className="row">{direction}</div>
    </>
  );
}

// Mirror of docs/mappings/v1_customer_mapping.yaml plus the order/inventory tables.
// Kept in the UI for inspection even when the API is offline; the source of truth
// is on disk and the engine loads it on startup.
function sampleFieldsForTable(table: string) {
  if (table === "customers") {
    return [
      { modern: "id", legacy: "CUST_ID", type: "string", direction: "both" },
      { modern: "email", legacy: "CUST_EMAIL", type: "string", direction: "both" },
      { modern: "name", legacy: "CUST_NAME", type: "string", direction: "both" },
      { modern: "company", legacy: "CUST_CO", type: "string", direction: "both" },
      { modern: "status", legacy: "STATUS_CD", type: "string", direction: "both" },
      { modern: "updated_at", legacy: "LAST_UPD_DT", type: "datetime", direction: "both" },
    ];
  }
  if (table === "orders") {
    return [
      { modern: "id", legacy: "ORD_ID", type: "string", direction: "both" },
      { modern: "customer_id", legacy: "CUST_ID", type: "string", direction: "both" },
      { modern: "order_date", legacy: "ORD_DT", type: "datetime", direction: "both" },
      { modern: "total_amount", legacy: "ORD_AMT", type: "numeric", direction: "both" },
      { modern: "status", legacy: "ORD_STAT", type: "string", direction: "both" },
    ];
  }
  if (table === "inventory") {
    return [
      { modern: "product_id", legacy: "PROD_CD", type: "string", direction: "both" },
      { modern: "quantity", legacy: "QTY_OH", type: "integer", direction: "both" },
      { modern: "last_restock", legacy: "LAST_REPL_DT", type: "datetime", direction: "both" },
    ];
  }
  return [];
}