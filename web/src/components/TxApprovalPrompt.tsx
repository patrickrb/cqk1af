import { useApprovals } from "@/store/approvals";
import { useEffect, useState } from "react";
import { apiPost } from "@/lib/api";

/**
 * Modal that surfaces pending TX approval requests.
 *
 * Design: clicking Approve or Deny immediately removes the request from the
 * pending queue (closes the modal) and fires the REST call in the background.
 * If the next approval arrives before the REST response returns, the modal
 * re-renders with the new request immediately — there's no in-flight state to
 * keep in sync.
 *
 * Auto-deny: setTimeout fires `decide(false)` at the expiry instant. Because
 * `decide` calls `resolve()` first, the same timer cannot fire twice for the
 * same request even on rapid re-renders.
 */
export function TxApprovalPrompt() {
  const pending = useApprovals((s) => s.pending);
  const resolve = useApprovals((s) => s.resolve);
  const top = pending[0];
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!top) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [top?.req_id]);

  function decide(req_id: string, approved: boolean) {
    // Close the modal first so the user sees the action register, and so the
    // same request can't be decided twice. The REST call resolves in the
    // background; 404 (already auto-denied/expired) is treated as success.
    resolve(req_id);
    apiPost("/api/control/approve_tx", { req_id, approved }).catch((e) => {
      console.warn("approve_tx failed", e);
    });
  }

  // Auto-deny on expiry — single-fire setTimeout per request. Uses the local
  // deadline computed at add-time so wall-clock skew between server and
  // browser can't auto-deny on first render.
  useEffect(() => {
    if (!top) return;
    const deadline = top.local_deadline_ms ?? new Date(top.expires_at).getTime();
    const ms = deadline - Date.now();
    if (ms <= 0) {
      console.warn("[approval] auto-deny on render", { req_id: top.req_id, ms, expires_at: top.expires_at, local_deadline_ms: top.local_deadline_ms });
      decide(top.req_id, false);
      return;
    }
    const t = setTimeout(() => decide(top.req_id, false), ms);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [top?.req_id, top?.local_deadline_ms]);

  if (!top) return null;

  const deadline = top.local_deadline_ms ?? new Date(top.expires_at).getTime();
  const remainingS = Math.max(0, Math.ceil((deadline - now) / 1000));

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60">
      <div className="card max-w-md w-full">
        <h3 className="text-lg font-bold text-tx mb-2">Approve transmit?</h3>
        <p className="text-sm text-slate-300 mb-4">{top.summary}</p>
        <pre className="text-xs bg-slate-950 rounded p-2 mb-3 overflow-x-auto">
          {JSON.stringify(top.payload, null, 2)}
        </pre>
        <div className="flex items-center justify-between">
          <div className="text-xs text-slate-400">
            Auto-deny in {remainingS}s · Queue: {pending.length}
          </div>
          <div className="flex gap-2">
            <button onClick={() => decide(top.req_id, false)} className="btn btn-secondary">
              Deny
            </button>
            <button onClick={() => decide(top.req_id, true)} className="btn btn-tx">
              Approve TX
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
