import { Link, Route, Routes, useLocation } from "react-router-dom";
import { useWebSocket } from "@/ws/client";
import { EmergencyStop } from "@/components/EmergencyStop";
import { TxApprovalPrompt } from "@/components/TxApprovalPrompt";
import { Operate } from "@/pages/Operate";
import { LogPage } from "@/pages/LogPage";
import { SettingsPage } from "@/pages/Settings";
import { useSession } from "@/store/session";

function Nav() {
  const loc = useLocation();
  const link = (to: string, label: string) => (
    <Link
      to={to}
      className={`px-3 py-1 rounded text-sm ${loc.pathname === to ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"}`}
    >
      {label}
    </Link>
  );
  return (
    <nav className="flex items-center gap-2">
      {link("/", "Operate")}
      {link("/log", "Log")}
      {link("/settings", "Settings")}
    </nav>
  );
}

export default function App() {
  useWebSocket();
  const callsign = useSession((s) => s.operator_callsign);
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-800 px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="font-bold tracking-wider">CQ{callsign}</h1>
          <Nav />
        </div>
        <div className="text-xs text-slate-500">v0.1</div>
      </header>
      <main className="flex-1 p-4">
        <Routes>
          <Route path="/" element={<Operate />} />
          <Route path="/log" element={<LogPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
      {/* Always-mounted safety surface */}
      <EmergencyStop />
      <TxApprovalPrompt />
    </div>
  );
}
