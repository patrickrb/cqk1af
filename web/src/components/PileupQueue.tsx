import { useState } from "react";
import { useTranscripts } from "@/store/transcripts";

// Pulls the FastAPI HTTPException detail.message out of the response when
// present, so we can surface "duplicate_callsign" / "partial_callsign" etc.
// directly in the row instead of a generic "400 Bad Request".
async function postOrThrow<T = unknown>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.ok) return r.json() as Promise<T>;
  let detail = `${r.status} ${r.statusText}`;
  try {
    const j = await r.json();
    if (j?.detail?.message) detail = j.detail.message;
    else if (typeof j?.detail === "string") detail = j.detail;
  } catch { /* non-JSON error body */ }
  throw new Error(detail);
}

interface ProbeResult {
  callsign: string;
  pattern: string;
  probe_text: string;
  tx_text: string;
  transmitted: boolean;
}

export function PileupQueue() {
  const pileup = useTranscripts((s) => s.pileup);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [addDraft, setAddDraft] = useState("");
  const [probeFor, setProbeFor] = useState<ProbeResult | null>(null);
  const [probeDraft, setProbeDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit(callsign: string) {
    setEditing(callsign);
    setDraft(callsign);
    setError(null);
  }

  function cancelEdit() {
    setEditing(null);
    setDraft("");
  }

  // Only A-Z, 0-9, ? are valid in a callsign (or partial). Uppercase as you type.
  function normalizeInput(s: string): string {
    return s.toUpperCase().replace(/[^A-Z0-9?]/g, "");
  }

  async function commitEdit(old: string) {
    const next = draft.trim();
    if (!next || next === old) {
      cancelEdit();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await postOrThrow("/api/control/pileup/edit", {
        old_callsign: old,
        new_callsign: next,
      });
      setEditing(null);
      setDraft("");
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function addEntry() {
    const next = addDraft.trim();
    if (!next) {
      setAdding(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await postOrThrow("/api/control/pileup/add", { callsign: next });
      setAdding(false);
      setAddDraft("");
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function removeEntry(callsign: string) {
    setBusy(true);
    setError(null);
    try {
      await postOrThrow("/api/control/pileup/remove", { callsign });
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function workOrProbe(callsign: string) {
    setBusy(true);
    setError(null);
    try {
      if (callsign.includes("?")) {
        // Fetch the probe preview; the operator can edit it before sending.
        const res = await postOrThrow<ProbeResult>("/api/control/pileup/probe", {
          callsign,
          dry_run: true,
        });
        setProbeFor(res);
        setProbeDraft(res.tx_text);
      } else {
        await postOrThrow("/api/control/select_caller", { callsign });
      }
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function sendProbe() {
    if (!probeFor) return;
    const text = probeDraft.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      await postOrThrow("/api/control/pileup/probe", {
        callsign: probeFor.callsign,
        dry_run: false,
        text_override: text,
      });
      setProbeFor(null);
      setProbeDraft("");
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
          Pileup ({pileup.length})
        </h2>
        <button
          onClick={() => { setAdding(true); setAddDraft(""); setError(null); }}
          disabled={busy || adding}
          className="text-xs text-slate-400 hover:text-slate-200 border border-slate-700 rounded px-2 py-0.5"
          title="Manually add a heard callsign (use ? for unknown characters)"
        >
          + Add
        </button>
      </div>

      {adding && (
        <div className="flex items-center gap-2 mb-2 bg-slate-800 rounded px-2 py-1">
          <input
            autoFocus
            value={addDraft}
            onChange={(e) => setAddDraft(normalizeInput(e.target.value))}
            onKeyDown={(e) => {
              if (e.key === "Enter") addEntry();
              if (e.key === "Escape") { setAdding(false); setAddDraft(""); }
            }}
            placeholder="K1?? — ? = unknown char"
            maxLength={10}
            className="bg-slate-900 border border-slate-700 px-2 py-0.5 rounded text-sm font-mono w-32"
          />
          <button
            onClick={addEntry}
            disabled={busy || !addDraft.trim()}
            className="btn btn-primary text-xs py-0.5 px-2"
          >
            Add
          </button>
          <button
            onClick={() => { setAdding(false); setAddDraft(""); }}
            className="text-xs text-slate-400 hover:text-slate-200"
          >
            Cancel
          </button>
        </div>
      )}

      {pileup.length === 0 && !adding ? (
        <div className="text-slate-500 text-sm">No callers heard.</div>
      ) : (
        <ul className="space-y-1">
          {pileup
            .slice()
            .sort((a, b) => b.confidence - a.confidence)
            .map((h) => {
              const isEditing = editing === h.callsign;
              const hasWildcard = h.callsign.includes("?");
              return (
                <li
                  key={h.callsign}
                  className="flex items-center justify-between bg-slate-800 rounded px-2 py-1 gap-2"
                >
                  {isEditing ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(normalizeInput(e.target.value))}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitEdit(h.callsign);
                        if (e.key === "Escape") cancelEdit();
                      }}
                      onBlur={() => commitEdit(h.callsign)}
                      maxLength={10}
                      className="bg-slate-900 border border-slate-600 px-2 py-0.5 rounded text-sm font-mono flex-1 min-w-0"
                    />
                  ) : (
                    <button
                      onClick={() => startEdit(h.callsign)}
                      disabled={busy}
                      title="Click to edit. Use ? for unknown characters."
                      className={`font-mono text-left hover:underline ${hasWildcard ? "text-yellow-300" : ""}`}
                    >
                      {h.callsign}
                    </button>
                  )}
                  <div className="flex items-center gap-2 text-xs text-slate-400 shrink-0">
                    <span>conf {(h.confidence * 100).toFixed(0)}%</span>
                    {h.snr_dbm != null && <span>{h.snr_dbm.toFixed(0)} dBm</span>}
                    <button
                      onClick={() => workOrProbe(h.callsign)}
                      disabled={busy}
                      className={`btn text-xs py-0.5 px-2 ${hasWildcard ? "btn-tx" : "btn-primary"}`}
                      title={hasWildcard
                        ? "Send a probe TX to fill in the missing characters"
                        : "Select this caller and start the QSO"}
                    >
                      {hasWildcard ? "Probe" : "Work"}
                    </button>
                    <button
                      onClick={() => removeEntry(h.callsign)}
                      disabled={busy}
                      className="text-slate-500 hover:text-kill"
                      title="Remove from pileup"
                    >
                      ×
                    </button>
                  </div>
                </li>
              );
            })}
        </ul>
      )}

      {probeFor && (
        <div className="mt-3 pt-3 border-t border-slate-800">
          <div className="text-xs text-slate-400 mb-1">
            Probe for <span className="font-mono text-yellow-300">{probeFor.callsign}</span>
            <span className="text-slate-600"> · {probeFor.pattern.replace(/_/g, " ")}</span>
          </div>
          <textarea
            value={probeDraft}
            onChange={(e) => setProbeDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                sendProbe();
              }
              if (e.key === "Escape") {
                setProbeFor(null);
                setProbeDraft("");
              }
            }}
            rows={2}
            className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm font-mono resize-y"
          />
          <div className="flex items-center justify-between mt-1">
            <span className="text-[10px] text-slate-500">
              Approval modal still gates the TX. Ctrl/⌘+Enter to send.
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => { setProbeFor(null); setProbeDraft(""); }}
                disabled={busy}
                className="text-xs text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={sendProbe}
                disabled={busy || !probeDraft.trim()}
                className="btn btn-tx text-xs"
              >
                Send probe
              </button>
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="text-kill text-xs mt-2 break-words">{error}</div>
      )}
    </div>
  );
}
