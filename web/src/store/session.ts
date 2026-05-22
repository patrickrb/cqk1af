import { create } from "zustand";
import type { AudioStatus, FSMState, SessionState } from "@/lib/types";

interface SessionStore {
  state: FSMState;
  armed: boolean;
  require_approval: boolean;
  operator_callsign: string;
  license_class: string;
  cq_attempts: number;
  qsos_completed: number;
  qsos_logged: number;
  error: string;
  audio: AudioStatus | null;
  rx_voice_active: boolean;
  setFromSnapshot(s: SessionState): void;
  setState(s: FSMState): void;
  setArmed(a: boolean): void;
  setRequireApproval(v: boolean): void;
  setAudioStatus(a: AudioStatus): void;
  setRxVoiceActive(active: boolean): void;
}

export const useSession = create<SessionStore>((set) => ({
  state: "DISCONNECTED",
  armed: false,
  require_approval: true,
  operator_callsign: "",
  license_class: "",
  cq_attempts: 0,
  qsos_completed: 0,
  qsos_logged: 0,
  error: "",
  audio: null,
  rx_voice_active: false,
  setFromSnapshot(s) {
    set({
      state: s.state,
      armed: s.armed,
      require_approval: s.require_approval ?? true,
      operator_callsign: s.operator_callsign,
      license_class: s.license_class,
      cq_attempts: s.cq_attempts,
      qsos_completed: s.qsos_completed,
      qsos_logged: s.qsos_logged,
      error: s.error,
      audio: s.audio ?? null,
    });
  },
  setState(state) { set({ state }); },
  setArmed(armed) { set({ armed }); },
  setRequireApproval(require_approval) { set({ require_approval }); },
  setAudioStatus(audio) { set({ audio }); },
  setRxVoiceActive(rx_voice_active) { set({ rx_voice_active }); },
}));
