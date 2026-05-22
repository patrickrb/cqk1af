import { create } from "zustand";
import type { RadioStatus, SliceInfo } from "@/lib/types";

interface RadioStore {
  connected: boolean;
  model: string;
  ptt_on: boolean;
  mic_source: string;
  slice: SliceInfo | null;
  s_meter_dbm: number | null;
  setFromStatus(r: RadioStatus): void;
  applyRadioSlice(p: Partial<SliceInfo> & { connected?: boolean; model?: string; ptt_on?: boolean }): void;
  setSMeter(dbm: number): void;
  setMicSource(s: string): void;
}

export const useRadio = create<RadioStore>((set, get) => ({
  connected: false,
  model: "",
  ptt_on: false,
  mic_source: "",
  slice: null,
  s_meter_dbm: null,
  setFromStatus(r) {
    set({
      connected: r.connected,
      model: r.model,
      ptt_on: r.ptt_on,
      mic_source: r.mic_source ?? "",
      slice: r.slices.length > 0 ? r.slices[0] : null,
    });
  },
  applyRadioSlice(p) {
    const cur = get();
    set({
      connected: p.connected !== undefined ? p.connected : cur.connected,
      model: p.model !== undefined ? p.model : cur.model,
      ptt_on: p.ptt_on !== undefined ? p.ptt_on : cur.ptt_on,
      slice: p.slice_id !== undefined
        ? {
            slice_id: p.slice_id,
            freq_hz: p.freq_hz ?? cur.slice?.freq_hz ?? 0,
            mode: p.mode ?? cur.slice?.mode ?? "",
            filter_low_hz: p.filter_low_hz ?? cur.slice?.filter_low_hz ?? 0,
            filter_high_hz: p.filter_high_hz ?? cur.slice?.filter_high_hz ?? 0,
          }
        : cur.slice,
    });
  },
  setSMeter(dbm) { set({ s_meter_dbm: dbm }); },
  setMicSource(mic_source) { set({ mic_source }); },
}));
