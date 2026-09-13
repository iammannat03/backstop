import Link from "next/link";
import { describeOutcome } from "@/lib/format";
import { isHold } from "@/lib/pipeline";
import type { Ticket } from "@/lib/types";
import { Age } from "./Age";
import { PipelineTrack } from "./PipelineTrack";

function accountName(ticket: Ticket): string {
  const meta = ticket.stripe_context?.customer_metadata;
  if (meta && typeof meta === "object" && "name" in meta && typeof meta.name === "string" && meta.name) {
    return meta.name;
  }
  return ticket.customer_email;
}

export function TicketsTable({ tickets }: { tickets: Ticket[] }) {
  return (
    <div className="overflow-x-auto border border-divider bg-[color-mix(in_srgb,var(--color-neutral-100)_60%,transparent)]">
      <div className="tickets-grid border-b border-divider bg-neutral-200 font-data text-[10px] tracking-[0.1em] text-neutral-600">
        <div className="px-3.5 py-2">TICKET</div>
        <div className="px-3.5 py-2">ACCOUNT</div>
        <div className="px-3.5 py-2">ACTION</div>
        <div className="px-3.5 py-2">PIPELINE</div>
        <div className="px-3.5 py-2 text-right">AGE</div>
      </div>
      {tickets.length === 0 && (
        <div className="px-3.5 py-8 text-center font-data text-sm text-neutral-500">
          No tickets match these filters.
        </div>
      )}
      {tickets.map((ticket, i) => {
        const hold = isHold(ticket.status);
        return (
          <Link
            key={ticket.id}
            href={`/tickets/${ticket.id}`}
            className="tickets-grid cursor-pointer border-b border-divider text-text last:border-b-0 hover:bg-[color-mix(in_srgb,var(--color-text)_4%,transparent)] hover:text-text"
            style={
              hold
                ? {
                    background: "color-mix(in srgb, var(--color-danger) 7%, transparent)",
                    boxShadow: "inset 3px 0 0 0 var(--color-danger)",
                  }
                : undefined
            }
          >
            <div className="px-3.5 py-[13px] font-data text-[12.5px] font-medium">BSP-{ticket.zendesk_ticket_id}</div>
            <div className="min-w-0 px-3.5 py-[13px]">
              <div className="truncate text-[14px] text-text">{accountName(ticket)}</div>
              <div className="font-data truncate text-[10.5px] text-neutral-600">
                {ticket.customer_id ?? "unresolved"}
              </div>
            </div>
            <div className="truncate px-3.5 py-[13px] text-[13.5px] text-neutral-800">
              {ticket.proposed_action ? describeOutcome(ticket.proposed_action) : "Awaiting proposal…"}
            </div>
            <div className="flex items-center px-3.5 py-[13px]">
              <PipelineTrack status={ticket.status} pulseDelay={`${(i * 0.31).toFixed(2)}s`} />
            </div>
            <div className="px-3.5 py-[13px] text-right font-data text-[12px] text-neutral-600">
              <Age fromIso={ticket.created_at} />
            </div>
          </Link>
        );
      })}
    </div>
  );
}
