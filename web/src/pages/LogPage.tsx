import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import type { QSOContext } from "@/lib/types";
import { formatFreq } from "@/lib/format";

export function LogPage() {
  const [qsos, setQsos] = useState<QSOContext[]>([]);
  useEffect(() => {
    apiGet<QSOContext[]>("/api/qsos").then(setQsos).catch(console.error);
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">QSO Log</h1>
        <a href="/api/qsos/export.adi" className="btn btn-secondary text-sm">Export ADIF</a>
      </div>
      <div className="card">
        <table className="w-full text-sm">
          <thead className="text-slate-400 text-xs uppercase">
            <tr>
              <th className="text-left py-1">Start</th>
              <th className="text-left py-1">Callsign</th>
              <th className="text-left py-1">Freq</th>
              <th className="text-left py-1">Mode</th>
              <th className="text-left py-1">RST tx/rx</th>
              <th className="text-left py-1">Name</th>
              <th className="text-left py-1">QTH</th>
            </tr>
          </thead>
          <tbody>
            {qsos.length === 0 && (
              <tr><td colSpan={7} className="text-slate-500 py-3">No QSOs yet.</td></tr>
            )}
            {qsos.map((q) => (
              <tr key={q.qso_id} className="border-t border-slate-800">
                <td className="py-1 font-mono text-xs">{q.start_ts.replace("T", " ").slice(0, 19)}</td>
                <td className="font-mono">{q.callsign}</td>
                <td className="font-mono">{formatFreq(q.freq_hz)}</td>
                <td>{q.mode}</td>
                <td>{q.rst_sent}/{q.rst_rcvd || "—"}</td>
                <td>{q.name || "—"}</td>
                <td>{q.qth || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
