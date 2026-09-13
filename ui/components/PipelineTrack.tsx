import { PIPELINE_STAGES, isDone, isHold, stageIndexFor, statusLabel } from "@/lib/pipeline";
import type { TicketStatus } from "@/lib/types";

export function PipelineTrack({ status, pulseDelay = "0s" }: { status: TicketStatus; pulseDelay?: string }) {
  const activeIndex = stageIndexFor(status);
  const hold = isHold(status);
  const inFlight = !hold && !isDone(status);

  return (
    <div className="flex shrink-0 items-center gap-2.5 whitespace-nowrap">
      <div className="flex gap-[3px]">
        {PIPELINE_STAGES.map((stage, i) => {
          let bg = "var(--color-neutral-300)";
          if (i < activeIndex) bg = "var(--color-accent)";
          else if (i === activeIndex) bg = hold ? "var(--color-danger)" : "var(--color-accent-400)";
          return (
            <span
              key={stage}
              className="h-[5px] w-[22px] min-w-[10px]"
              style={{
                background: bg,
                animation: i === activeIndex && inFlight ? `bsPulse 1.5s ease-in-out ${pulseDelay} infinite` : "none",
              }}
            />
          );
        })}
      </div>
      <span className="flex min-w-0 items-center gap-1.5">
        <span
          className="h-1.5 w-1.5 shrink-0"
          style={{
            background: hold ? "var(--color-danger)" : inFlight ? "var(--color-accent)" : "var(--color-neutral-300)",
            animation: inFlight || hold ? `bsPulse 1.5s ease-in-out ${pulseDelay} infinite` : "none",
          }}
        />
        <span
          className="font-data text-[11px] tracking-[0.05em] uppercase"
          style={{
            color: hold ? "var(--color-danger)" : "var(--color-neutral-700)",
            fontWeight: hold ? 500 : 400,
          }}
        >
          {statusLabel(status)}
        </span>
      </span>
    </div>
  );
}
