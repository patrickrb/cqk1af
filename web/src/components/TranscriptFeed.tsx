import { useEffect, useRef } from "react";
import { useTranscripts } from "@/store/transcripts";

export function TranscriptFeed() {
  const entries = useTranscripts((s) => s.entries);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [entries.length]);

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
        Transcript
      </h2>
      <div ref={ref} className="h-64 overflow-y-auto bg-slate-950 rounded p-2 font-mono text-sm space-y-1">
        {entries.length === 0 && (
          <div className="text-slate-600">
            No audio transcribed yet — RX STT lights up once you wire DAX RX + faster-whisper. TX
            transmissions are narrated here as the assistant plans them.
          </div>
        )}
        {entries.map((e, i) => {
          const isTx = e.direction === "tx";
          return (
            <div key={i} className="flex gap-2 items-start">
              <span className="text-slate-500 text-xs shrink-0 mt-0.5">
                {new Date(e.ts).toLocaleTimeString()}
              </span>
              <span
                className={`text-xs px-1 rounded shrink-0 mt-0.5 ${
                  isTx ? "bg-tx/30 text-tx" : "bg-rx/20 text-rx"
                }`}
              >
                {isTx ? "TX" : "RX"}
              </span>
              <span
                className={
                  e.confidence > 0.8
                    ? "text-slate-100"
                    : e.confidence > 0.5
                      ? "text-yellow-200"
                      : "text-slate-400"
                }
              >
                {e.text}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
