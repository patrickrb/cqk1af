import { useSession } from "@/store/session";
import { apiPost } from "@/lib/api";
import { useState } from "react";
import type { SessionState } from "@/lib/types";

export function SessionControls() {
  const armed = useSession((s) => s.armed);
  const state = useSession((s) => s.state);
  const requireApproval = useSession((s) => s.require_approval);
  const setFromSnapshot = useSession((s) => s.setFromSnapshot);
  const setRequireApproval = useSession((s) => s.setRequireApproval);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function arm(value: boolean) {
    setError(null);
    setBusy(true);
    try {
      const updated = await apiPost<SessionState>("/api/control/arm", { armed: value });
      if (updated && typeof updated.armed === "boolean") {
        setFromSnapshot(updated);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setError(null);
    setBusy(true);
    try {
      const updated = await apiPost<SessionState>("/api/control/reset");
      if (updated) setFromSnapshot(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleRequireApproval(next: boolean) {
    setError(null);
    // Optimistic: flip UI immediately, revert on error.
    setRequireApproval(next);
    try {
      await apiPost("/api/control/require_approval", { armed: next });
    } catch (e) {
      setRequireApproval(!next);
      setError(String(e));
    }
  }

  const canArm = state !== "DISCONNECTED" && state !== "STOPPED" && state !== "ERROR";
  const needsReset = state === "STOPPED" || state === "ERROR";

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Session</h2>
      <div className="flex items-center gap-3 mb-3">
        {needsReset ? (
          <button
            onClick={reset}
            disabled={busy}
            className="btn btn-secondary disabled:opacity-50"
            title="Return to IDLE so you can arm and start again"
          >
            {busy ? "…" : state === "ERROR" ? "Acknowledge & reset" : "Reset to IDLE"}
          </button>
        ) : armed ? (
          <button
            onClick={() => arm(false)}
            disabled={busy}
            className="btn btn-secondary disabled:opacity-50"
            title="Disarm — block all transmit"
          >
            {busy ? "…" : "Disarm"}
          </button>
        ) : (
          <button
            onClick={() => arm(true)}
            disabled={!canArm || busy}
            className="btn btn-tx disabled:opacity-40"
            title={canArm ? "Arm session — TX is now possible" : "Connect the radio first"}
          >
            {busy ? "…" : "Arm session"}
          </button>
        )}
        <span className="text-xs text-slate-400">
          {needsReset
            ? state === "ERROR"
              ? "An error occurred. Acknowledge to continue."
              : "Session stopped. Reset to start a new session."
            : armed
              ? requireApproval
                ? "TX gated by per-action approval"
                : "Autonomous TX — assistant transmits without per-message approval"
              : canArm
                ? "Default: no transmission permitted"
                : "Connect the radio first"}
        </span>
      </div>

      <label className="flex items-center gap-2 text-xs cursor-pointer select-none">
        <input
          type="checkbox"
          checked={requireApproval}
          onChange={(e) => toggleRequireApproval(e.target.checked)}
          className="accent-tx"
        />
        <span className={requireApproval ? "text-slate-200" : "text-yellow-200"}>
          Require approval for each TX
        </span>
        {!requireApproval && (
          <span className="text-yellow-300 text-[10px] uppercase tracking-wider">
            Autonomous
          </span>
        )}
      </label>
      <div className="text-[11px] text-slate-500 mt-1 ml-5">
        {requireApproval
          ? "Operator clicks Approve on every TX. Safer; chatty."
          : "Assistant TXes without asking. Kill switch is the only gate."}
      </div>

      {error && <div className="text-kill text-xs mt-2">{error}</div>}
    </div>
  );
}
