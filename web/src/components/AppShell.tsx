import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { data, getDataSource, type DataSource } from "../lib/data";
import type { HealthResponse } from "../types";

interface AppShellProps {
  children: React.ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [source, setSource] = useState<DataSource>("demo");
  const [pendingCount, setPendingCount] = useState<number>(0);
  const location = useLocation();

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [h, pending] = await Promise.all([
          data.health(),
          data.listPendingConflicts(),
        ]);
        if (cancelled) return;
        setHealth(h);
        setSource(getDataSource());
        setPendingCount(pending.length);
      } catch {
        if (cancelled) return;
        setSource("demo");
      }
    }
    refresh();
    const id = setInterval(refresh, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const onConflictsRoute = location.pathname.startsWith("/conflicts");
  const showResolved = location.pathname.endsWith("/resolved");

  return (
    <div className="app-shell">
      <div className="app-shell-inner">
        <aside className="app-sidebar" aria-label="primary navigation">
          <Link to="/conflicts" className="brand" aria-label="legacy-sync-engine home">
            <span className="brand-mark" aria-hidden>↔</span>
            <span className="brand-text">
              <span className="name">legacy-sync</span>
              <span className="sub">v0.1 · reconciliation console</span>
            </span>
          </Link>

          <div className="nav-section" role="navigation" aria-label="workspace">
            <div className="nav-label">workspace</div>
            <NavLink to="/conflicts" end className="nav-link">
              conflicts
              {pendingCount > 0 && <span className="count">{pendingCount}</span>}
            </NavLink>
            <NavLink to="/conflicts/resolved" className="nav-link">
              resolved
            </NavLink>
            <NavLink to="/audit" className="nav-link">audit log</NavLink>
            <NavLink to="/trigger" className="nav-link">trigger sync</NavLink>
            <NavLink to="/mappings" className="nav-link">mappings</NavLink>
          </div>

          <div style={{ marginTop: "auto", paddingTop: "var(--sp-6)" }}>
            <div className="source-banner" aria-live="polite">
              <span className="source-dot" aria-hidden />
              {source === "live" ? "live · api reachable" : "demo · offline fixtures"}
            </div>
          </div>
        </aside>

        <header className="app-header">
          <div className="app-header-title">
            <strong>legacy-sync-engine</strong>
            <span className="sep">/</span>
            {pageName(location.pathname)}
            {onConflictsRoute && (
              <>
                <span className="sep">/</span>
                <span>{showResolved ? "resolved" : "pending"}</span>
              </>
            )}
          </div>
          <div className="app-header-meta">
            <span>modern: {health?.modern_db ?? "?"}</span>
            <span>·</span>
            <span>legacy: {health?.legacy_db ?? "?"}</span>
            <span>·</span>
            <span>
              mappings:{" "}
              {health?.mapping_versions.length
                ? `${health.mapping_versions.length} loaded`
                : "—"}
            </span>
          </div>
        </header>

        <main className="app-main">{children}</main>
      </div>
      <div className="hint-bar" aria-hidden>
        press <kbd style={{
          background: "var(--bg-elev-2)",
          padding: "1px 6px",
          borderRadius: "3px",
          border: "1px solid var(--border-faint)",
          fontSize: "var(--fs-xs)",
        }}>?</kbd> for keymap · v0.1
      </div>
    </div>
  );
}

function pageName(path: string): string {
  if (path.startsWith("/conflicts")) return "conflicts";
  if (path.startsWith("/audit")) return "audit";
  if (path.startsWith("/trigger")) return "trigger sync";
  if (path.startsWith("/mappings")) return "mappings";
  return "overview";
}