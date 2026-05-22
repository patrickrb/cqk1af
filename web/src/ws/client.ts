import { useEffect, useRef } from "react";
import type { WSMessage } from "@/lib/types";
import { useSession } from "@/store/session";
import { useRadio } from "@/store/radio";
import { useTranscripts } from "@/store/transcripts";
import { useApprovals } from "@/store/approvals";

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

type SendFn = (m: Record<string, unknown>) => void;

let _send: SendFn = () => { console.warn("ws not connected; drop"); };
export function wsSend(type: string, payload: Record<string, unknown> = {}) {
  _send({ type, payload });
}

export function useWebSocket(): { connected: boolean } {
  const ref = useRef<WebSocket | null>(null);
  const connectedRef = useRef(false);
  useEffect(() => {
    let stopped = false;
    let retry = 0;

    const connect = () => {
      if (stopped) return;
      const ws = new WebSocket(WS_URL);
      ref.current = ws;

      _send = (m) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m));
      };

      ws.onopen = () => {
        retry = 0;
        connectedRef.current = true;
      };
      ws.onclose = () => {
        connectedRef.current = false;
        if (stopped) return;
        const delay = Math.min(15000, 500 * Math.pow(2, retry++));
        setTimeout(connect, delay);
      };
      ws.onerror = () => { /* close handler will trigger reconnect */ };
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data) as WSMessage;
          dispatch(msg);
        } catch (e) {
          console.warn("invalid ws message", e);
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      ref.current?.close();
    };
  }, []);

  return { connected: connectedRef.current };
}

// S-meter coalescing: keep only the latest value per animation frame so a
// burst of meter messages produces at most one React render per frame.
let _pendingSMeterDbm: number | null = null;
let _smeterRafScheduled = false;
function scheduleSMeterFlush() {
  if (_smeterRafScheduled) return;
  _smeterRafScheduled = true;
  requestAnimationFrame(() => {
    _smeterRafScheduled = false;
    if (_pendingSMeterDbm != null) {
      useRadio.getState().setSMeter(_pendingSMeterDbm);
      _pendingSMeterDbm = null;
    }
  });
}

function dispatch(msg: WSMessage) {
  switch (msg.type) {
    case "hello":
      useSession.getState().setFromSnapshot(msg.payload.state);
      useRadio.getState().setFromStatus(msg.payload.radio);
      // Re-seed in-flight approvals so a WS reconnect doesn't strand the operator.
      useApprovals.getState().clear();
      for (const a of msg.payload.pending_approvals ?? []) {
        useApprovals.getState().add(a);
      }
      break;
    case "state_changed":
      useSession.getState().setState(msg.payload.next);
      // Clear pileup when QSO concludes
      if (msg.payload.next === "LOGGED" || msg.payload.next === "STOPPED" || msg.payload.next === "IDLE") {
        useTranscripts.getState().clearPileup();
      }
      // Kill switch path forces disarm
      if (msg.payload.next === "STOPPED") {
        useSession.getState().setArmed(false);
      }
      // Targeted updates flow via dedicated WS messages (qso_started/updated/
      // logged, session_armed, radio_slice, etc.) — no full-state refetch per
      // transition.
      break;
    case "session_armed":
      useSession.getState().setArmed(msg.payload.armed);
      break;
    case "radio_slice":
      useRadio.getState().applyRadioSlice(msg.payload);
      break;
    case "s_meter":
      _pendingSMeterDbm = msg.payload.dbm;
      scheduleSMeterFlush();
      break;
    case "transcript":
      useTranscripts.getState().addTranscript({
        text: msg.payload.text,
        confidence: msg.payload.confidence,
        ts: msg.payload.ts,
        direction: msg.payload.direction ?? "rx",
      });
      break;
    case "callsigns_heard":
      useTranscripts.getState().appendPileup(msg.payload.entries);
      break;
    case "tx_approval_requested":
      useApprovals.getState().add(msg.payload);
      break;
    case "qso_started":
      useTranscripts.getState().setQSOCallsign(msg.payload.callsign);
      break;
    case "qso_logged":
      useTranscripts.getState().setQSOCallsign(null);
      break;
    case "qso_updated":
      // dashboard refreshes on next state poll
      break;
    case "error":
      console.warn("server error msg", msg.payload);
      break;
  }
}
