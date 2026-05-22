import { RadioPanel } from "@/components/RadioPanel";
import { StateBadge } from "@/components/StateBadge";
import { PileupQueue } from "@/components/PileupQueue";
import { TranscriptFeed } from "@/components/TranscriptFeed";
import { StatsPanel } from "@/components/StatsPanel";
import { SessionControls } from "@/components/SessionControls";
import { OperatingPanel } from "@/components/OperatingPanel";
import { PendingLogEntry } from "@/components/PendingLogEntry";

export function Operate() {
  return (
    <div className="grid grid-cols-12 gap-4">
      <div className="col-span-12 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Operating</h1>
        <StateBadge />
      </div>
      <div className="col-span-12 md:col-span-4 space-y-4">
        <SessionControls />
        <RadioPanel />
        <StatsPanel />
      </div>
      <div className="col-span-12 md:col-span-4 space-y-4">
        <OperatingPanel />
        <PileupQueue />
      </div>
      <div className="col-span-12 md:col-span-4 space-y-4">
        <PendingLogEntry />
        <TranscriptFeed />
      </div>
    </div>
  );
}
