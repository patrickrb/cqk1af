import { create } from "zustand";

export interface ApprovalRequest {
  req_id: string;
  summary: string;
  payload: Record<string, unknown>;
  expires_at: string;
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
    set({ pending: [...get().pending, r] });
  },
  resolve(req_id) {
    set({ pending: get().pending.filter((p) => p.req_id !== req_id) });
  },
  clear() { set({ pending: [] }); },
}));
