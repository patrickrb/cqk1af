export type FSMState =
  | "DISCONNECTED"
  | "IDLE"
  | "SCANNING"
  | "LISTENING"
  | "CHECKING_FREQUENCY"
  | "CALLING_CQ"
  | "RECEIVING_REPLY"
  | "HANDLING_PILEUP"
  | "IN_QSO"
  | "CONFIRMING_LOG"
  | "LOGGED"
  | "STOPPED"
  | "ERROR";

export type LicenseClass = "Extra" | "Advanced" | "General" | "Technician" | "Novice";

export interface HeardCallsign {
  callsign: string;
  confidence: number;
  snr_dbm: number | null;
}

export interface QSOContext {
  qso_id: string;
  callsign: string;
  freq_hz: number;
  mode: string;
  band: string;
  rst_sent: string;
  rst_rcvd: string;
  name: string;
  qth: string;
  grid: string;
  comment: string;
  start_ts: string;
  end_ts: string;
}

export interface AudioStatus {
  enabled: boolean;
  rx_capture: string;
  tx_playback: string;
  stt_engine: string;
  tts_engine: string;
  rx_device: string;
  tx_device: string;
  piper_bin: string;
  can_rx: boolean;
  can_tx: boolean;
  notes: string[];
  frames_seen?: number;
  voice_frames?: number;
  voice_utterances?: number;
  stt_attempts?: number;
  stt_results?: number;
  stt_dropped?: number;
  last_stt_error?: string;
  rx_rms_dbfs?: number;
}

export interface SessionState {
  state: FSMState;
  armed: boolean;
  require_approval: boolean;
  operator_callsign: string;
  license_class: LicenseClass;
  current_slice_id: number;
  current_freq_hz: number;
  current_mode: string;
  current_band: string;
  pileup: HeardCallsign[];
  current_qso: QSOContext | null;
  cq_attempts: number;
  qsos_completed: number;
  qsos_logged: number;
  error: string;
  audio?: AudioStatus | null;
}

export interface RadioStatus {
  connected: boolean;
  model: string;
  version: string;
  ptt_on: boolean;
  mic_source: string;
  slices: SliceInfo[];
}

export interface SliceInfo {
  slice_id: number;
  freq_hz: number;
  mode: string;
  filter_low_hz: number;
  filter_high_hz: number;
}

export type WSMessage =
  | {
      type: "hello";
      payload: {
        session_id: string;
        state: SessionState;
        radio: RadioStatus;
        pending_approvals?: Array<{
          req_id: string;
          summary: string;
          payload: Record<string, unknown>;
          expires_at: string;
        }>;
      };
    }
  | { type: "state_changed"; payload: { prev: FSMState; next: FSMState; reason: string; ts: string } }
  | { type: "radio_slice"; payload: Partial<SliceInfo> & { connected?: boolean; model?: string; ptt_on?: boolean } }
  | { type: "s_meter"; payload: { slice_id: number; dbm: number } }
  | { type: "transcript"; payload: { text: string; confidence: number; direction?: "tx" | "rx"; ts: string } }
  | { type: "audio_status"; payload: AudioStatus }
  | { type: "rx_voice"; payload: { active: boolean; channel: string; duration_ms?: number } }
  | { type: "callsigns_heard"; payload: { entries: HeardCallsign[] } }
  | { type: "pileup_sync"; payload: { entries: HeardCallsign[] } }
  | { type: "tx_approval_requested"; payload: { req_id: string; summary: string; payload: Record<string, unknown>; expires_at: string; timeout_s?: number } }
  | { type: "qso_started"; payload: { qso_id: string; callsign: string; freq_hz: number; mode: string } }
  | { type: "qso_updated"; payload: { qso_id: string; fields: Record<string, unknown> } }
  | { type: "qso_logged"; payload: { qso_id: string; cloudlog_id: string | null } }
  | { type: "session_armed"; payload: { armed: boolean } }
  | { type: "error"; payload: { message: string; severity: "info" | "warn" | "error" } };
