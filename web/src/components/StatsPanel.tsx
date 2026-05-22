import { memo } from "react";
import { useSession } from "@/store/session";

export const StatsPanel = memo(function StatsPanel() {
  // Scoped selectors — only re-render when the displayed field changes.
  const operator_callsign = useSession((s) => s.operator_callsign);
  const license_class = useSession((s) => s.license_class);
  const cq_attempts = useSession((s) => s.cq_attempts);
  const qsos_completed = useSession((s) => s.qsos_completed);
  const qsos_logged = useSession((s) => s.qsos_logged);
  const error = useSession((s) => s.error);

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Stats</h2>
      <dl className="grid grid-cols-2 gap-y-1 text-sm">
        <dt className="text-slate-400">Operator</dt>
        <dd className="font-mono">{operator_callsign || "—"} ({license_class || "?"})</dd>
        <dt className="text-slate-400">CQ attempts</dt>
        <dd>{cq_attempts}</dd>
        <dt className="text-slate-400">QSOs completed</dt>
        <dd>{qsos_completed}</dd>
        <dt className="text-slate-400">QSOs logged</dt>
        <dd>{qsos_logged}</dd>
        {error && (
          <>
            <dt className="text-kill">Error</dt>
            <dd className="text-kill text-xs">{error}</dd>
          </>
        )}
      </dl>
    </div>
  );
});
