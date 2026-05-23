import { useState } from "react";
import { useSession } from "@/store/session";
import { apiPost } from "@/lib/api";
import type { SessionState } from "@/lib/types";

const HIDDEN_EVENTS = new Set(["kill", "stop", "pileup_selected"]);

export function ManualDispatch() {
  const state = useSession((s) => s.state);
  const validEvents = useSession((s) => s.valid_events);
  const setFromSnapshot = useSession((s) => s.setFromSnapshot);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const events = (validEvents ?? []).filter((e) => !HIDDEN_EVENTS.has(e));

  async function dispatch(event: string) {
    setError(null);
    setBusy(event);
    try {
      const updated = await apiPost<SessionState>("/api/control/dispatch_event", {
        event,
        reason: "manual",
      });
      if (updated) setFromSnapshot(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
        Manual event dispatch
      </h2>
      <div className="text-xs text-slate-400 mb-3">
        Force an FSM event from <span className="font-mono text-slate-200">{state}</span>. Only
        events the state machine allows from the current state are shown. Use the kill switch
        for emergency stop, and the pileup list to pick a caller.
      </div>
      {events.length === 0 ? (
        <div className="text-xs text-slate-500 italic">
          No manual events are valid from {state}.
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {events.map((event) => (
            <button
              key={event}
              onClick={() => dispatch(event)}
              disabled={busy !== null}
              className="btn btn-secondary text-xs disabled:opacity-40"
              title={`Dispatch ${event} from ${state}`}
            >
              {busy === event ? "…" : event}
            </button>
          ))}
        </div>
      )}
      {error && <div className="text-kill text-xs mt-2">{error}</div>}
    </div>
  );
}
