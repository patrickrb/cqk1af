import { useCallback, useState } from "react";
import { apiPost } from "@/lib/api";
import { wsSend } from "@/ws/client";
import { useSession } from "@/store/session";

/**
 * Always-mounted kill switch. Sends BOTH a REST POST and a WS message for
 * redundancy — whichever arrives first wins. Operator can mash it without
 * worrying about transport.
 */
export function EmergencyStop() {
  const state = useSession((s) => s.state);
  const [busy, setBusy] = useState(false);

  const onClick = useCallback(async () => {
    setBusy(true);
    wsSend("kill", { reason: "dashboard.EmergencyStop" });
    try {
      await apiPost("/api/control/kill", { reason: "dashboard.EmergencyStop" });
    } catch (e) {
      console.error("kill REST failed", e);
    } finally {
      setBusy(false);
    }
  }, []);

  const isStopped = state === "STOPPED";
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="fixed bottom-4 right-4 z-50 btn btn-kill rounded-full w-24 h-24 shadow-lg shadow-kill/50 text-lg flex items-center justify-center disabled:opacity-50"
      aria-label="Emergency stop"
      title={isStopped ? "Already stopped" : "Emergency stop — release PTT and halt the session"}
    >
      {busy ? "…" : isStopped ? "STOPPED" : "STOP"}
    </button>
  );
}
