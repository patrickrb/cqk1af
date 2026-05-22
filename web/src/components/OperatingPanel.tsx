import { useEffect, useState } from "react";
import { apiPost } from "@/lib/api";
import { useSession } from "@/store/session";
import { useRadio } from "@/store/radio";
import { formatFreq } from "@/lib/format";

interface Candidate {
  freq_hz: number;
  band: string;
  mode: string;
}

const BANDS = ["80m", "40m", "20m", "17m", "15m", "12m", "10m"];

/**
 * State-aware operating panel. Surfaces the next sensible action based on the
 * FSM state. Each click calls a single backend tool; everything else (TX
 * approval, kill switch) flows through existing components.
 */
export function OperatingPanel() {
  // Scoped selectors so we don't re-render on every 4Hz S-meter tick.
  const state = useSession((s) => s.state);
  const armed = useSession((s) => s.armed);
  const audio = useSession((s) => s.audio);
  const radioModel = useRadio((s) => s.model);
  const sliceFreqHz = useRadio((s) => s.slice?.freq_hz ?? 0);
  const micSource = useRadio((s) => s.mic_source);
  const connected = useRadio((s) => s.connected);

  const [band, setBand] = useState("20m");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [simCall, setSimCall] = useState("W1AW");
  const [manualText, setManualText] = useState("");
  const [manualBusy, setManualBusy] = useState(false);

  // SSB sideband convention: LSB below 10 MHz, USB above.
  const effectiveMode: "USB" | "LSB" = band === "80m" || band === "40m" ? "LSB" : "USB";

  // We're in mock mode if the radio's model string says so. Real FlexRadio
  // 6400 shows the actual model.
  const isMockMode = radioModel.toLowerCase().includes("mock");

  // Fetch band candidates only when the band actually changes — not on every
  // unrelated re-render. AbortController cancels in-flight requests if the user
  // changes bands quickly.
  useEffect(() => {
    setError(null);
    const ac = new AbortController();
    fetch(`/api/radio/candidates/${band}?mode=${effectiveMode}`, { signal: ac.signal })
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setCandidates(d))
      .catch(() => { /* aborted or transport error */ });
    return () => ac.abort();
  }, [band, effectiveMode]);

  async function call<T>(path: string, body?: unknown): Promise<T | null> {
    setError(null);
    setBusy(true);
    try {
      return await apiPost<T>(path, body);
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function checkFreq(freq_hz: number) {
    await call("/api/radio/tune", { freq_hz, mode: effectiveMode });
    await call("/api/operate/check_frequency", { freq_hz, attempts: 3 });
  }

  async function callCq() {
    await call("/api/operate/call_cq", { attempts: 1 });
  }

  async function listenForReply() {
    await call("/api/operate/listen_for_reply");
  }

  async function sendReport(rst: string) {
    await call("/api/operate/exchange_signal_report", { rst_sent: rst });
  }

  async function completeQso() {
    await call("/api/operate/complete_qso", { closing_phrase: "73" });
  }

  async function sendManualTx() {
    const text = manualText.trim();
    if (!text) return;
    setError(null);
    setManualBusy(true);
    try {
      await apiPost("/api/operate/manual_tx", { text });
      setManualText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setManualBusy(false);
    }
  }

  async function simulateCaller() {
    await call("/api/operate/simulate_caller", {
      callsign: simCall.trim().toUpperCase(),
      confidence: 0.92,
      snr_dbm: -55,
    });
  }

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
        Operate <span className="text-slate-600 text-xs">— {state}</span>
      </h2>

      <AudioBanner audio={audio} isMockMode={isMockMode} />

      {connected && !isMockMode && micSource && micSource !== "DAX" && (
        <div className="mb-3 text-xs bg-kill/20 border border-kill/60 text-red-200 rounded px-2 py-1.5">
          <div className="font-semibold mb-1">
            Radio is set to <span className="font-mono">Mic Input = {micSource}</span>, not DAX.
          </div>
          <div>
            The radio will key when you transmit but your TX audio will be silent on the air.
            In SmartSDR, open <span className="font-mono">Settings → MIC</span> and set{" "}
            <span className="font-mono">Input Source</span> to <span className="font-mono">DAX</span>.
            (Persistent across sessions.)
          </div>
        </div>
      )}

      {!armed && (
        <div className="text-slate-400 text-sm">
          Arm the session before starting. Workflow is gated behind per-TX approval.
        </div>
      )}

      {armed && (state === "IDLE" || state === "SCANNING" || state === "LOGGED") && (
        <>
          <div className="flex items-center gap-2 mb-3 text-sm">
            <label className="text-slate-400 text-xs">Band</label>
            <select
              value={band}
              onChange={(e) => setBand(e.target.value)}
              className="bg-slate-800 border border-slate-700 px-2 py-1 rounded text-sm"
            >
              {BANDS.map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
            <span className="text-slate-500 text-xs">({effectiveMode})</span>
          </div>
          <div className="max-h-48 overflow-y-auto border border-slate-800 rounded">
            <table className="w-full text-sm">
              <tbody>
                {candidates.length === 0 && (
                  <tr><td className="py-2 px-2 text-slate-500">No candidates.</td></tr>
                )}
                {candidates.slice(0, 12).map((c) => (
                  <tr key={c.freq_hz} className="border-t border-slate-800 hover:bg-slate-800">
                    <td className="font-mono py-1 px-2">{formatFreq(c.freq_hz)}</td>
                    <td className="text-right py-1 px-2">
                      <button
                        onClick={() => checkFreq(c.freq_hz)}
                        disabled={busy}
                        className="btn btn-primary text-xs"
                      >
                        Check this freq
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {armed && state === "LISTENING" && (
        <div className="text-cyan-300 text-sm">
          Listening on {formatFreq(sliceFreqHz)} for activity…
        </div>
      )}

      {armed && state === "CHECKING_FREQUENCY" && (
        <div className="text-yellow-300 text-sm">
          Asking "Is this frequency in use?" — approve each TX request from the modal.
        </div>
      )}

      {armed && state === "CALLING_CQ" && (
        <div className="flex flex-col gap-2 text-sm">
          <div className="text-tx">
            Ready to call CQ on {formatFreq(sliceFreqHz)}. Approve the TX modal to
            narrate the CQ.
          </div>
          <button onClick={callCq} disabled={busy} className="btn btn-tx w-full">
            Call CQ
          </button>
        </div>
      )}

      {armed && (state === "RECEIVING_REPLY" || state === "HANDLING_PILEUP") && (
        <div className="flex flex-col gap-2 text-sm">
          {state === "RECEIVING_REPLY" ? (
            <div className="text-cyan-300">
              Listening for reply. Without RX STT wired, the pileup will not populate
              from the air — use Simulate caller (mock mode) or wait for STT to be configured.
            </div>
          ) : (
            <div className="text-slate-400">
              Pick a station from the Pileup panel to start the QSO.
            </div>
          )}
          {isMockMode && (
            <div className="flex items-center gap-2 mt-1">
              <input
                value={simCall}
                onChange={(e) => setSimCall(e.target.value)}
                className="bg-slate-800 border border-slate-700 px-2 py-1 rounded text-sm font-mono w-32"
                placeholder="W1AW"
              />
              <button
                onClick={simulateCaller}
                disabled={busy || !simCall.trim()}
                className="btn btn-secondary text-xs"
                title="Inject a CallsignHeard event so the pileup workflow can be exercised"
              >
                Simulate caller
              </button>
              {state === "RECEIVING_REPLY" && (
                <button
                  onClick={listenForReply}
                  disabled={busy}
                  className="btn btn-secondary text-xs"
                >
                  Resolve replies
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {armed && state === "IN_QSO" && (
        <div className="flex flex-col gap-2 text-sm">
          <div className="text-ok">QSO in progress. Send a signal report, then close.</div>
          <div className="flex gap-2">
            {["59", "57", "55"].map((r) => (
              <button
                key={r}
                onClick={() => sendReport(r)}
                disabled={busy}
                className="btn btn-tx"
              >
                Send {r}
              </button>
            ))}
          </div>
          <button onClick={completeQso} disabled={busy} className="btn btn-secondary">
            Complete QSO (73)
          </button>
        </div>
      )}

      {armed && (
        <div className="mt-3 pt-3 border-t border-slate-800">
          <label className="text-xs uppercase tracking-wider text-slate-400 mb-1 block">
            Manual TX — operator correction
          </label>
          <textarea
            value={manualText}
            onChange={(e) => setManualText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                sendManualTx();
              }
            }}
            placeholder="Type what to say on the air. Callsigns like K1AF are auto-phoneticized."
            rows={2}
            disabled={manualBusy}
            className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm font-mono resize-y"
          />
          <div className="flex items-center justify-between mt-1">
            <span className="text-[10px] text-slate-500">
              Approval modal still gates the TX. Ctrl/⌘+Enter to send.
            </span>
            <button
              onClick={sendManualTx}
              disabled={manualBusy || !manualText.trim()}
              className="btn btn-tx text-xs"
            >
              {manualBusy ? "Sending…" : "Send TX"}
            </button>
          </div>
        </div>
      )}

      {error && <div className="text-kill text-xs mt-2 break-words">{error}</div>}
    </div>
  );
}

interface AudioStatusLike {
  enabled: boolean;
  rx_capture: string;
  tx_playback: string;
  stt_engine: string;
  tts_engine: string;
  rx_device: string;
  tx_device: string;
  can_rx: boolean;
  can_tx: boolean;
  notes: string[];
}

function AudioBanner({
  audio,
  isMockMode,
}: {
  audio: AudioStatusLike | null | undefined;
  isMockMode: boolean;
}) {
  if (!audio) {
    return (
      <div className="mb-3 text-xs bg-slate-800 border border-slate-700 text-slate-400 rounded px-2 py-1.5">
        Audio status: not reported yet (connect the radio to initialise).
      </div>
    );
  }

  const tone = audio.can_tx && audio.can_rx
    ? "bg-green-900/30 border-green-700/50 text-green-200"
    : audio.can_tx || audio.can_rx
      ? "bg-yellow-900/30 border-yellow-700/50 text-yellow-200"
      : "bg-slate-800 border-slate-700 text-slate-300";

  // The root cause matters. If the pipeline is off entirely, mentioning Piper
  // or DAX devices is noise. Surface the real blocker first.
  const pipelineOff = !audio.enabled;

  return (
    <div className={`mb-3 text-xs border rounded px-2 py-1.5 ${tone}`}>
      <div className="flex items-center gap-3">
        <span>
          <span className="font-semibold">TX:</span>{" "}
          {audio.can_tx
            ? `${audio.tts_engine} → ${audio.tx_device || "?"}`
            : audio.tx_playback}
        </span>
        <span>·</span>
        <span>
          <span className="font-semibold">RX:</span>{" "}
          {audio.can_rx
            ? `${audio.rx_device || "?"} → ${audio.stt_engine}`
            : audio.rx_capture}
        </span>
      </div>
      {pipelineOff && !isMockMode && (
        <div className="mt-1">
          Audio pipeline is <span className="font-mono">disabled</span> in config. Set{" "}
          <span className="font-mono">audio_pipeline_enabled: true</span> in{" "}
          <span className="font-mono">config/default.yaml</span> (or{" "}
          <span className="font-mono">~/.cqk1af/config.yaml</span>) and restart the backend.
        </div>
      )}
      {!pipelineOff && !audio.can_tx && !isMockMode && audio.tts_engine !== "piper" && (
        <div className="mt-1">
          On-air TX disabled — Piper not detected. Install{" "}
          <span className="font-mono">piper.exe</span> on PATH and place a voice model in{" "}
          <span className="font-mono">runtime/piper/</span>.
        </div>
      )}
      {!pipelineOff && !audio.can_tx && !isMockMode &&
       audio.tts_engine === "piper" && audio.tx_playback !== "ok" && (
        <div className="mt-1">
          TX device not found ({audio.tx_playback}). Is SmartSDR running with DAX TX enabled?
        </div>
      )}
      {!pipelineOff && !audio.can_rx && !isMockMode && audio.rx_capture !== "ok" && (
        <div className="mt-1">
          RX device not found ({audio.rx_capture}). Is SmartSDR running with DAX RX 1 enabled?
        </div>
      )}
      {isMockMode && (
        <div className="mt-1 italic">
          Mock radio mode — no audio devices used. Use{" "}
          <span className="font-mono">Simulate caller</span> to drive the pileup flow.
        </div>
      )}
      {audio.notes && audio.notes.length > 0 && (
        <ul className="mt-1 list-disc pl-4 space-y-0.5">
          {audio.notes.slice(0, 3).map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
