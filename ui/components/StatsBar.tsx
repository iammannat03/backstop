import type { QueueStats } from "@/lib/types";
import { BlueprintCorners } from "./Blueprint";

function Stat({
  label,
  value,
  suffix,
}: {
  label: string;
  value: string;
  suffix?: string;
}) {
  return (
    <div className="blueprint px-4 py-[14px]">
      <BlueprintCorners />
      <div className="font-data text-[10px] tracking-[0.1em] text-neutral-600">{label}</div>
      <div className="font-heading mt-1 text-[34px] leading-[1.05]">
        {value}
        {suffix && <span className="text-[16px] text-neutral-600">{suffix}</span>}
      </div>
    </div>
  );
}

export function StatsBar({ stats }: { stats: QueueStats }) {
  const medianDisplay = stats.medianCycleSeconds == null ? "N/A" : stats.medianCycleSeconds.toFixed(1);
  const mismatchDisplay =
    stats.verifierMismatchRatePct == null ? "N/A" : stats.verifierMismatchRatePct.toFixed(1);

  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-[14px]">
      <Stat label="IN FLIGHT" value={String(stats.inFlight)} />
      <Stat label="AUTO-EXECUTED · TODAY" value={String(stats.autoExecutedToday)} />
      <Stat label="MEDIAN CYCLE" value={medianDisplay} suffix="s" />
      <Stat label="VERIFIER MISMATCH RATE" value={mismatchDisplay} suffix="%" />
    </div>
  );
}
