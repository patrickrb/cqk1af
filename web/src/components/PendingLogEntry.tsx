import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { useSession } from "@/store/session";
import type { QSOContext, SessionState } from "@/lib/types";

/**
 * Surfaces the open QSO (or pending-log) for inline edit + confirmation.
 * Active in IN_QSO, CONFIRMING_LOG.
 */
export function PendingLogEntry() {
  const state = useSession((s) => s.state);
  const [qso, setQso] = useState<QSOContext | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (state !== "IN_QSO" && state !== "CONFIRMING_LOG") {
      setQso(null);
      return;
    }
    apiGet<SessionState>("/api/radio/state")
      .then((s) => setQso(s.current_qso))
      .catch(() => setQso(null));
  }, [state]);

  if (state !== "IN_QSO" && state !== "CONFIRMING_LOG") return null;
  if (!qso) return null;

  function patch<K extends keyof QSOContext>(field: K, value: QSOContext[K]) {
    if (!qso) return;
    setQso({ ...qso, [field]: value });
  }

  async function saveField(field: string, value: string) {
    setError(null);
    try {
      await apiPost("/api/control/qso/field", { field, value });
    } catch (e) {
      setError(String(e));
    }
  }

  async function confirmLog() {
    setError(null);
    setBusy(true);
    try {
      const edits = {
        callsign: qso?.callsign,
        rst_sent: qso?.rst_sent,
        rst_rcvd: qso?.rst_rcvd,
        name: qso?.name,
        qth: qso?.qth,
        grid: qso?.grid,
        comment: qso?.comment,
      };
      await apiPost("/api/operate/confirm_log", { edits });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const editing = state === "IN_QSO" || state === "CONFIRMING_LOG";

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
        {state === "CONFIRMING_LOG" ? "Confirm log" : "Active QSO"}
      </h2>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <Field label="Callsign" value={qso.callsign}
          onChange={(v) => patch("callsign", v.toUpperCase())}
          onBlur={(v) => saveField("callsign", v.toUpperCase())}
          disabled={!editing} mono />
        <Field label="RST sent" value={qso.rst_sent}
          onChange={(v) => patch("rst_sent", v)}
          onBlur={(v) => saveField("rst_sent", v)}
          disabled={!editing} mono />
        <Field label="RST rcvd" value={qso.rst_rcvd}
          onChange={(v) => patch("rst_rcvd", v)}
          onBlur={(v) => saveField("rst_rcvd", v)}
          disabled={!editing} mono />
        <Field label="Name" value={qso.name}
          onChange={(v) => patch("name", v)}
          onBlur={(v) => saveField("name", v)}
          disabled={!editing} />
        <Field label="QTH" value={qso.qth}
          onChange={(v) => patch("qth", v)}
          onBlur={(v) => saveField("qth", v)}
          disabled={!editing} />
        <Field label="Grid" value={qso.grid}
          onChange={(v) => patch("grid", v.toUpperCase())}
          onBlur={(v) => saveField("grid", v.toUpperCase())}
          disabled={!editing} mono />
        <Field label="Comment" value={qso.comment}
          onChange={(v) => patch("comment", v)}
          onBlur={(v) => saveField("comment", v)}
          disabled={!editing}
          fullWidth />
      </div>
      {state === "CONFIRMING_LOG" && (
        <button onClick={confirmLog} disabled={busy} className="btn btn-primary mt-3 w-full">
          {busy ? "…" : "Confirm & log QSO"}
        </button>
      )}
      {error && <div className="text-kill text-xs mt-2">{error}</div>}
    </div>
  );
}

function Field({
  label, value, onChange, onBlur, disabled, mono, fullWidth,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onBlur: (v: string) => void;
  disabled?: boolean;
  mono?: boolean;
  fullWidth?: boolean;
}) {
  return (
    <label className={`flex flex-col ${fullWidth ? "col-span-2" : ""}`}>
      <span className="text-slate-400 text-xs">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={(e) => onBlur(e.target.value)}
        disabled={disabled}
        className={`bg-slate-800 border border-slate-700 px-2 py-1 rounded ${mono ? "font-mono" : ""}`}
      />
    </label>
  );
}
