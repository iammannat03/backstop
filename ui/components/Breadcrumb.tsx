import Link from "next/link";

// Sits above the ticket detail view, reuses site-header.tsx's styling so it
// reads as part of the same header system.
export function Breadcrumb({ ticketLabel }: { ticketLabel: string }) {
  return (
    <div className="flex items-center gap-2 border-b border-divider bg-neutral-100 px-[22px] py-2.5 font-data text-[12px] text-neutral-600">
      <Link href="/" className="hover:text-accent hover:underline">
        Tickets
      </Link>
      <span className="text-neutral-400">/</span>
      <span className="text-text">{ticketLabel}</span>
    </div>
  );
}
