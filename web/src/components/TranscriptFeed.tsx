import { useEffect, useMemo, useRef } from "react";
import { useTranscripts } from "@/store/transcripts";
import { useSession } from "@/store/session";
import type { AudioStatus } from "@/lib/types";

interface RxStatus {
  level: "ready" | "pending" | "off";
  label: string;
  detail: string;
}

function describeRx(audio: AudioStatus | null): RxStatus {
  if (!audio) {
    return {
      level: "pending",
      label: "Connecting…",
      detail: "Waiting for audio pipeline status.",
    };
  }
  if (!audio.enabled) {
    return {
      level: "off",
      label: "RX disabled",
      detail: "Audio pipeline disabled in config (audio_pipeline_enabled=false).",
    };
  }
  if (audio.can_rx) {
    const engine = audio.stt_engine === "whisper" ? "faster-whisper" : audio.stt_engine;
    return {
      level: "ready",
      label: "RX STT live",
      detail: `Listening on ${audio.rx_device || "DAX RX"} → ${engine}.`,
    };
  }
  const reason =
    audio.rx_capture !== "ok"
      ? audio.rx_capture
      : audio.stt_engine === "disabled"
        ? "STT disabled"
        : audio.stt_engine !== "whisper" && audio.stt_engine !== "mock"
          ? `STT: ${audio.stt_engine}`
          : "RX not ready";
  return { level: "off", label: "RX not wired", detail: reason };
}

function RxIndicator({ status, voiceActive }: { status: RxStatus; voiceActive: boolean }) {
  const dotColor =
    status.level === "ready"
      ? voiceActive
        ? "bg-rx shadow-[0_0_8px_var(--tw-shadow-color)] shadow-rx"
        : "bg-rx/70"
      : status.level === "pending"
        ? "bg-warn"
        : "bg-kill";
  const labelColor =
    status.level === "ready"
      ? "text-rx"
      : status.level === "pending"
        ? "text-warn"
        : "text-slate-500";
  return (
    <span
      className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider"
      title={status.detail}
    >
      <span
        className={`inline-block w-2 h-2 rounded-full ${dotColor} ${
          status.level === "ready" && voiceActive ? "animate-pulse" : ""
        }`}
      />
      <span className={labelColor}>{status.label}</span>
    </span>
  );
}

function formatDbfs(dbfs: number | undefined): string {
  if (dbfs === undefined || dbfs <= -119) return "—";
  return `${dbfs.toFixed(0)} dBFS`;
}

function PipelineMetrics({ audio }: { audio: AudioStatus | null }) {
  if (!audio) return null;
  const voiceFrames = audio.voice_frames ?? 0;
  const utterances = audio.voice_utterances ?? 0;
  const results = audio.stt_results ?? 0;
  const dropped = audio.stt_dropped ?? 0;
  if (voiceFrames === 0 && utterances === 0 && results === 0 && dropped === 0) {
    return (
      <div className="text-[11px] text-slate-500 mt-1">
        RX RMS {formatDbfs(audio.rx_rms_dbfs)} · no voice frames yet
      </div>
    );
  }
  return (
    <div className="text-[11px] text-slate-500 mt-1">
      RX RMS {formatDbfs(audio.rx_rms_dbfs)} · vad frames {voiceFrames} · utterances {utterances} · stt ok {results} · dropped {dropped}
      {audio.last_stt_error ? ` · last drop: ${audio.last_stt_error}` : ""}
    </div>
  );
}

export function TranscriptFeed() {
  const entries = useTranscripts((s) => s.entries);
  const audio = useSession((s) => s.audio);
  const voiceActive = useSession((s) => s.rx_voice_active);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [entries.length]);

  const rxStatus = useMemo(() => describeRx(audio), [audio]);

  const emptyText =
    rxStatus.level === "ready"
      ? `${rxStatus.detail} TX transmissions are narrated here as the assistant plans them.`
      : rxStatus.level === "pending"
        ? rxStatus.detail
        : `${rxStatus.detail} TX transmissions still narrate here as the assistant plans them.`;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
          Transcript
        </h2>
        <RxIndicator status={rxStatus} voiceActive={voiceActive} />
      </div>
      <PipelineMetrics audio={audio} />
      <div
        ref={ref}
        className="h-64 overflow-y-auto bg-slate-950 rounded p-2 font-mono text-sm space-y-1 mt-2"
      >
        {entries.length === 0 && (
          <div className="text-slate-600">{emptyText}</div>
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
