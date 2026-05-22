import { create } from "zustand";

export interface ApprovalRequest {
  req_id: string;
  summary: string;
  payload: Record<string, unknown>;
  expires_at: string;
  timeout_s?: number;
  // Locally computed deadline (ms since epoch from Date.now()) — set when the
  // request is added so the modal's auto-deny is immune to clock skew between
  // the server clock (expires_at) and the browser clock.
  local_deadline_ms?: number;
}

interface ApprovalStore {
  pending: ApprovalRequest[];
  add(r: ApprovalRequest): void;
  resolve(req_id: string): void;
  clear(): void;
}

export const useApprovals = create<ApprovalStore>((set, get) => ({
  pending: [],
  add(r) {
    if (get().pending.find((p) => p.req_id === r.req_id)) return;
    // Compute the local deadline once at add-time. Prefer the server's
    // timeout_s (relative, clock-skew immune); fall back to expires_at minus
    // wall clock so old servers still work, with a 5s floor so a stale clock
    // can't auto-deny instantly.
    const skewSafeMs = typeof r.timeout_s === "number"
      ? r.timeout_s * 1000
      : Math.max(5000, new Date(r.expires_at).getTime() - Date.now());
    const local_deadline_ms = Date.now() + skewSafeMs;
    set({ pending: [...get().pending, { ...r, local_deadline_ms }] });
  },
  resolve(req_id) {
    set({ pending: get().pending.filter((p) => p.req_id !== req_id) });
  },
  clear() { set({ pending: [] }); },
}));
