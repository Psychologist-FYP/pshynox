import type { Health } from "../../lib/api";

export function StatusPill({ health, compact = false }: { health: Health | null; compact?: boolean }) {
  return (
    <span className={`status-pill ${health ? "is-online" : "is-offline"} ${compact ? "is-compact" : ""}`}>
      {health ? (health.fixture_mode ? "Example data enabled" : "Saved locally") : "Workspace unavailable"}
    </span>
  );
}
