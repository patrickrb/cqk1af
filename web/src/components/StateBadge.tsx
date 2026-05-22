import type { FSMState } from "@/lib/types";
import { useSession } from "@/store/session";

const COLORS: Record<FSMState, string> = {
  DISCONNECTED: "bg-slate-700",
  IDLE: "bg-slate-600",
  SCANNING: "bg-blue-600",
  LISTENING: "bg-cyan-600",
  CHECKING_FREQUENCY: "bg-yellow-600",
  CALLING_CQ: "bg-tx",
  RECEIVING_REPLY: "bg-cyan-500",
  HANDLING_PILEUP: "bg-purple-600",
  IN_QSO: "bg-ok",
  CONFIRMING_LOG: "bg-amber-500",
  LOGGED: "bg-emerald-500",
  STOPPED: "bg-kill",
  ERROR: "bg-red-800",
};

export function StateBadge() {
  const state = useSession((s) => s.state);
  const armed = useSession((s) => s.armed);
  return (
    <div className="flex items-center gap-3">
      <span
        className={`inline-flex items-center px-3 py-1 rounded text-white font-bold tracking-wider ${COLORS[state] ?? "bg-slate-600"}`}
      >
        {state}
      </span>
      <span
        className={`text-xs px-2 py-0.5 rounded border ${armed ? "border-tx text-tx" : "border-slate-600 text-slate-400"}`}
      >
        {armed ? "ARMED" : "SAFE"}
      </span>
    </div>
  );
}
