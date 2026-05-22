import { useTranscripts } from "@/store/transcripts";
import { apiPost } from "@/lib/api";

export function PileupQueue() {
  const pileup = useTranscripts((s) => s.pileup);

  async function pick(callsign: string) {
    try {
      await apiPost("/api/control/select_caller", { callsign });
    } catch (e) {
      console.error("select_caller failed", e);
    }
  }

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
        Pileup ({pileup.length})
      </h2>
      {pileup.length === 0 ? (
        <div className="text-slate-500 text-sm">No callers heard.</div>
      ) : (
        <ul className="space-y-1">
          {pileup
            .slice()
            .sort((a, b) => b.confidence - a.confidence)
            .map((h) => (
              <li
                key={h.callsign}
                className="flex items-center justify-between bg-slate-800 rounded px-2 py-1"
              >
                <div className="font-mono">{h.callsign}</div>
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <span>conf {(h.confidence * 100).toFixed(0)}%</span>
                  {h.snr_dbm != null && <span>{h.snr_dbm.toFixed(0)} dBm</span>}
                  <button onClick={() => pick(h.callsign)} className="btn btn-primary text-xs py-0.5 px-2">
                    Work
                  </button>
                </div>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
