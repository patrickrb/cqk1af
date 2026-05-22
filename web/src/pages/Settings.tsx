import { useEffect, useState } from "react";
import { apiGet, apiPatch } from "@/lib/api";

interface SettingsBlob {
  operator: { callsign: string; name: string; qth: string; grid_square: string; license_class: string; phonetic_alphabet: string };
  safety: { require_manual_tx_approval: boolean; approval_timeout_s: number; max_transmit_seconds: number; armed_default: boolean; id_interval_seconds: number; kill_releases_ptt: boolean };
  cq: { template_id: string; max_attempts: number; listen_seconds: number; inter_call_min_delay_s: number; inter_call_max_delay_s: number };
  cloudlog: { enabled: boolean; url: string; station_profile_id: number };
  operator_license_class_options: string[];
}

export function SettingsPage() {
  const [s, setS] = useState<SettingsBlob | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    apiGet<SettingsBlob>("/api/settings").then(setS).catch((e) => setErr(String(e)));
  }, []);

  function set<K extends string>(key: K, value: unknown) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function save() {
    setErr(null);
    setSaved(null);
    try {
      const r = await apiPatch("/api/settings", draft);
      setSaved(JSON.stringify(r));
      setDraft({});
      const refreshed = await apiGet<SettingsBlob>("/api/settings");
      setS(refreshed);
    } catch (e) {
      setErr(String(e));
    }
  }

  if (!s) return <div className="text-slate-400">Loading…</div>;

  return (
    <div className="space-y-4 max-w-2xl">
      <h1 className="text-xl font-semibold">Settings</h1>

      <div className="card">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Operator</h2>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">Callsign</span>
            <input className="bg-slate-800 px-2 py-1 rounded border border-slate-700 font-mono"
              defaultValue={s.operator.callsign}
              onChange={(e) => set("operator_callsign", e.target.value.toUpperCase())} />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">License class</span>
            <select className="bg-slate-800 px-2 py-1 rounded border border-slate-700"
              defaultValue={s.operator.license_class}
              onChange={(e) => set("operator_license_class", e.target.value)}>
              {s.operator_license_class_options.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">Name</span>
            <input className="bg-slate-800 px-2 py-1 rounded border border-slate-700"
              defaultValue={s.operator.name}
              onChange={(e) => set("operator_name", e.target.value)} />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">QTH</span>
            <input className="bg-slate-800 px-2 py-1 rounded border border-slate-700"
              defaultValue={s.operator.qth}
              onChange={(e) => set("operator_qth", e.target.value)} />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">Grid square</span>
            <input className="bg-slate-800 px-2 py-1 rounded border border-slate-700 font-mono"
              defaultValue={s.operator.grid_square}
              onChange={(e) => set("operator_grid_square", e.target.value.toUpperCase())} />
          </label>
        </div>
      </div>

      <div className="card">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Safety</h2>
        <label className="flex items-center gap-2 text-sm mb-2">
          <input type="checkbox" defaultChecked={s.safety.require_manual_tx_approval}
            onChange={(e) => set("safety_require_manual_tx_approval", e.target.checked)} />
          Require manual TX approval for every transmission
        </label>
        <label className="flex flex-col text-sm">
          <span className="text-slate-400 text-xs">Approval timeout (s)</span>
          <input type="number" className="bg-slate-800 px-2 py-1 rounded border border-slate-700 w-24"
            defaultValue={s.safety.approval_timeout_s}
            onChange={(e) => set("safety_approval_timeout_s", parseInt(e.target.value, 10))} />
        </label>
      </div>

      <div className="card">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">CQ</h2>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">Template</span>
            <input className="bg-slate-800 px-2 py-1 rounded border border-slate-700"
              defaultValue={s.cq.template_id}
              onChange={(e) => set("cq_template_id", e.target.value)} />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">Max attempts</span>
            <input type="number" className="bg-slate-800 px-2 py-1 rounded border border-slate-700"
              defaultValue={s.cq.max_attempts}
              onChange={(e) => set("cq_max_attempts", parseInt(e.target.value, 10))} />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-400 text-xs">Listen seconds</span>
            <input type="number" className="bg-slate-800 px-2 py-1 rounded border border-slate-700"
              defaultValue={s.cq.listen_seconds}
              onChange={(e) => set("cq_listen_seconds", parseInt(e.target.value, 10))} />
          </label>
        </div>
      </div>

      <div className="card">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Cloudlog</h2>
        <p className="text-xs text-slate-400 mb-2">URL and API key are configured in the YAML file (not editable here for security).</p>
        <label className="flex flex-col text-sm">
          <span className="text-slate-400 text-xs">Station profile ID</span>
          <input type="number" className="bg-slate-800 px-2 py-1 rounded border border-slate-700 w-24"
            defaultValue={s.cloudlog.station_profile_id}
            onChange={(e) => set("cloudlog_station_profile_id", parseInt(e.target.value, 10))} />
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button onClick={save} className="btn btn-primary">Save</button>
        {saved && <span className="text-ok text-xs">Saved.</span>}
        {err && <span className="text-kill text-xs">{err}</span>}
      </div>
    </div>
  );
}
