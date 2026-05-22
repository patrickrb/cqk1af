import { memo, useState } from "react";
import { useRadio } from "@/store/radio";
import { formatDbm, formatFreq } from "@/lib/format";
import { apiPost } from "@/lib/api";

/**
 * RadioPanel — split into stable parts and one tiny S-meter component that
 * subscribes to the high-rate field. The wrapper re-renders only when one of
 * its scoped selectors changes; the meter ticker re-renders 4 Hz on its own.
 */
export const RadioPanel = memo(function RadioPanel() {
  // Each selector is shallow-compared, so the outer panel re-renders only when
  // the *value* actually changes. We deliberately do NOT read s_meter_dbm here.
  const connected = useRadio((s) => s.connected);
  const model = useRadio((s) => s.model);
  const pttOn = useRadio((s) => s.ptt_on);
  const freqHz = useRadio((s) => s.slice?.freq_hz ?? 0);
  const mode = useRadio((s) => s.slice?.mode ?? "");
  const sliceId = useRadio((s) => s.slice?.slice_id ?? 0);

  const [freqInput, setFreqInput] = useState("14250.000");
  const [tuneMode, setTuneMode] = useState<"USB" | "LSB">("USB");
  const [error, setError] = useState<string | null>(null);

  async function tune() {
    setError(null);
    const mhz = parseFloat(freqInput);
    if (isNaN(mhz)) { setError("invalid frequency"); return; }
    try {
      await apiPost("/api/radio/tune", {
        freq_hz: Math.round(mhz * 1_000_000),
        mode: tuneMode,
        slice_id: sliceId,
      });
    } catch (e) {
      setError(String(e));
    }
  }

  async function connect() {
    setError(null);
    try { await apiPost("/api/radio/connect"); }
    catch (e) { setError(String(e)); }
  }
  async function disconnect() {
    setError(null);
    try { await apiPost("/api/radio/disconnect"); }
    catch (e) { setError(String(e)); }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Radio</h2>
        <div className="flex items-center gap-2 text-xs">
          <span className={connected ? "text-ok" : "text-slate-500"}>
            ● {connected ? "connected" : "disconnected"}
          </span>
          {pttOn && <span className="text-tx font-bold">TX</span>}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-3 text-sm">
        <div>
          <div className="text-slate-400 text-xs">Frequency</div>
          <div className="font-mono text-lg">{formatFreq(freqHz)}</div>
        </div>
        <div>
          <div className="text-slate-400 text-xs">Mode</div>
          <div className="font-mono text-lg">{mode || "—"}</div>
        </div>
        <SMeterCell />
        <div>
          <div className="text-slate-400 text-xs">Model</div>
          <div className="text-sm">{model || "—"}</div>
        </div>
      </div>

      <div className="flex gap-2 mb-2">
        <input
          type="text"
          value={freqInput}
          onChange={(e) => setFreqInput(e.target.value)}
          placeholder="MHz"
          className="bg-slate-800 border border-slate-700 px-2 py-1 rounded font-mono text-sm w-32"
        />
        <select
          value={tuneMode}
          onChange={(e) => setTuneMode(e.target.value as "USB" | "LSB")}
          className="bg-slate-800 border border-slate-700 px-2 py-1 rounded text-sm"
        >
          <option value="USB">USB</option>
          <option value="LSB">LSB</option>
        </select>
        <button onClick={tune} className="btn btn-secondary">Tune</button>
      </div>
      <div className="flex gap-2 text-xs">
        {connected ? (
          <button onClick={disconnect} className="btn btn-secondary">Disconnect</button>
        ) : (
          <button onClick={connect} className="btn btn-primary">Connect</button>
        )}
      </div>
      {error && <div className="text-kill text-xs mt-2">{error}</div>}
    </div>
  );
});

/** Self-contained S-meter — the only thing that re-renders at the meter's update rate. */
const SMeterCell = memo(function SMeterCell() {
  const dbm = useRadio((s) => s.s_meter_dbm);
  return (
    <div>
      <div className="text-slate-400 text-xs">S-Meter</div>
      <div className="font-mono">{formatDbm(dbm)}</div>
      {dbm != null && (
        <div className="h-2 mt-1 bg-slate-800 rounded overflow-hidden">
          <div
            className="h-full bg-cyan-500 transition-all"
            style={{ width: `${Math.min(100, Math.max(0, (dbm + 120) * 2))}%` }}
          />
        </div>
      )}
    </div>
  );
});
