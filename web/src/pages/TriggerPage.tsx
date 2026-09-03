import { useState } from "react";
import { data } from "../lib/data";
import type { SyncTriggerRequest, SyncTriggerResponse } from "../types";

const TABLES = ["customers", "orders", "inventory"] as const;
const DIRECTIONS = ["both", "modern_to_legacy", "legacy_to_modern"] as const;

export default function TriggerPage() {
  const [table, setTable] = useState<(typeof TABLES)[number]>("customers");
  const [direction, setDirection] = useState<(typeof DIRECTIONS)[number]>("both");
  const [busy, setBusy] = useState<boolean>(false);
  const [result, setResult] = useState<SyncTriggerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const req: SyncTriggerRequest = { table, direction };
      const res = await data.triggerSync(req);
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-eyebrow">admin · control</div>
          <h1 className="page-title">trigger sync</h1>
          <p className="page-sub">
            Force a poller pass for a table, in one direction or both. In production this kicks off the
            CDC cycle: read change-log / updated_at since the last watermark, apply the versioned mapping,
            and write the idempotent upsert. Idempotent on <code>job_id</code>.
          </p>
        </div>
      </div>

      <div className="trigger-grid">
        <form className="trigger-form" onSubmit={handleSubmit} aria-label="trigger sync form">
          <div className="field-block">
            <label htmlFor="table">table</label>
            <select
              id="table"
              value={table}
              onChange={(e) => setTable(e.target.value as (typeof TABLES)[number])}
            >
              {TABLES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          <div className="field-block">
            <label>direction</label>
            <div className="chip-row">
              {DIRECTIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`chip${direction === d ? " active" : ""}`}
                  onClick={() => setDirection(d)}
                  aria-pressed={direction === d}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>

          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "queueing…" : "queue sync job"}
          </button>
        </form>

        <div>
          {error && (
            <div className="error-strip" role="alert" style={{ marginBottom: "var(--sp-3)" }}>
              <span className="label">failed</span>
              {error}
            </div>
          )}
          <div className="page-eyebrow" style={{ marginBottom: "var(--sp-2)" }}>response</div>
          <div className="result-block" aria-live="polite">
            {result
              ? JSON.stringify(result, null, 2)
              : "// awaiting trigger. the response from POST /api/sync/trigger will appear here, with job_id and status."}
          </div>

          <div style={{ marginTop: "var(--sp-4)" }}>
            <div className="page-eyebrow" style={{ marginBottom: "var(--sp-2)" }}>what runs</div>
            <ol
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "var(--fs-sm)",
                color: "var(--fg-secondary)",
                paddingLeft: "var(--sp-5)",
                lineHeight: 1.8,
              }}
            >
              <li>Read sync_state watermark for {table}</li>
              <li>Poll modern_saas for updated rows &gt; watermark</li>
              <li>Poll legacy_erp.CHANGE_LOG for entries &gt; watermark</li>
              <li>
                Apply{" "}
                <code style={{ color: "var(--accent-primary)" }}>
                  v1.0/{table}
                </code>{" "}
                mapping YAML
              </li>
              <li>Detect conflict against opposing side within 5s skew window</li>
              <li>Apply strategy: LWW / SOT / queue for manual review</li>
              <li>Upsert idempotently (event_id dedup + version check)</li>
              <li>Append to audit_log + DLQ on retry exhaustion</li>
            </ol>
          </div>
        </div>
      </div>
    </>
  );
}