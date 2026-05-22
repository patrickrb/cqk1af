import { create } from "zustand";
import type { HeardCallsign } from "@/lib/types";

interface TranscriptEntry {
  text: string;
  confidence: number;
  ts: string;
  direction?: "tx" | "rx";
}

interface TranscriptStore {
  entries: TranscriptEntry[];
  pileup: HeardCallsign[];
  current_qso_callsign: string | null;
  addTranscript(t: TranscriptEntry): void;
  setPileup(p: HeardCallsign[]): void;
  appendPileup(p: HeardCallsign[]): void;
  setQSOCallsign(c: string | null): void;
  clearPileup(): void;
}

export const useTranscripts = create<TranscriptStore>((set, get) => ({
  entries: [],
  pileup: [],
  current_qso_callsign: null,
  addTranscript(t) {
    set({ entries: [...get().entries.slice(-99), t] });
  },
  setPileup(pileup) { set({ pileup }); },
  appendPileup(entries) {
    const cur = get().pileup;
    const merged = [...cur];
    for (const e of entries) {
      if (!merged.find((m) => m.callsign.toUpperCase() === e.callsign.toUpperCase())) {
        merged.push(e);
      }
    }
    set({ pileup: merged });
  },
  setQSOCallsign(c) { set({ current_qso_callsign: c }); },
  clearPileup() { set({ pileup: [] }); },
}));
