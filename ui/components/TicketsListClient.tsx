"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { usePolling } from "@/lib/usePolling";
import type { QueueStats, Ticket } from "@/lib/types";
import { Pagination } from "./Pagination";
import { StatsBar } from "./StatsBar";
import { TicketsFilters } from "./TicketsFilters";
import { TicketsTable } from "./TicketsTable";

interface ListData {
  tickets: Ticket[];
  total: number;
  page: number;
  pageSize: number;
}

// Height of the table header row and the least height a ticket row needs for
// its two lines of text without clipping.
const HEADER_PX = 34;
const MIN_ROW_PX = 56;

export function TicketsListClient({
  initial,
  initialStats,
  canSync,
}: {
  initial: ListData;
  initialStats: QueueStats;
  canSync: boolean;
}) {
  const searchParams = useSearchParams();
  const query = searchParams.toString();

  // The table never scrolls, it is paginated, so the page size follows the
  // room available and every row on a page is fully visible.
  const tableBoxRef = useRef<HTMLDivElement>(null);
  const [fitRows, setFitRows] = useState<number | null>(null);
  useEffect(() => {
    const el = tableBoxRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const rows = Math.floor((el.clientHeight - HEADER_PX) / MIN_ROW_PX);
      setFitRows(Math.max(3, Math.min(20, rows)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Polling URL tracks the current filters/page, so changing a filter
  // naturally starts polling the newly filtered endpoint.
  const pollParams = new URLSearchParams(query);
  if (fitRows) pollParams.set("pageSize", String(fitRows));
  const pollQuery = pollParams.toString();
  const [{ tickets, total, page, pageSize }, refetchTickets] = usePolling<ListData>(
    `/api/tickets${pollQuery ? `?${pollQuery}` : ""}`,
    initial,
  );
  const [stats, refetchStats] = usePolling<QueueStats>("/api/stats", initialStats);

  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);

  async function handleRefresh() {
    setSyncing(true);
    setSyncResult(null);
    try {
      const res = await fetch("/api/poll-now", { method: "POST" });
      const json = await res.json();
      setSyncResult(
        json.error ? `Sync failed: ${json.error}` : `Synced: ${json.dispatched} new ticket${json.dispatched === 1 ? "" : "s"}`,
      );
    } catch {
      setSyncResult("Could not reach the ingestion service.");
    }
    await Promise.all([refetchTickets(), refetchStats()]);
    setSyncing(false);
    setTimeout(() => setSyncResult(null), 5000);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-none px-[22px] pt-6">
        <StatsBar stats={stats} />
      </div>
      <div className="mt-5 flex min-h-0 flex-1 flex-col overflow-hidden px-[22px] pb-6">
        {canSync && (
          <div className="flex shrink-0 flex-wrap items-center gap-2.5 border-b border-divider bg-neutral-100 px-[22px] py-2.5">
            <button disabled={syncing} onClick={handleRefresh} className="btn btn-secondary text-[12px]">
              {syncing ? "Syncing…" : "↻ Refresh"}
            </button>
            <span className="font-data text-[11px] text-neutral-600">
              {syncResult ?? "Pulls new tickets from Zendesk right now instead of waiting for the poller"}
            </span>
          </div>
        )}
        <div className="shrink-0">
          <TicketsFilters
            // Remounts the filter inputs when the URL's filters change, since
            // they're uncontrolled (defaultValue) and won't otherwise re-sync.
            key={query}
            initialQ={searchParams.get("q") ?? ""}
            initialCustomerId={searchParams.get("customerId") ?? ""}
            initialStatus={searchParams.get("status") ?? "all"}
            initialActionType={searchParams.get("type") ?? "all"}
            initialDateFrom={searchParams.get("dateFrom") ?? ""}
            initialDateTo={searchParams.get("dateTo") ?? ""}
          />
        </div>
        {/* The grid divides its height equally between rows, which only works
            when that height is definite, so it fills an absolutely positioned
            box instead of relying on flex percentage heights. */}
        <div ref={tableBoxRef} className="relative min-h-0 flex-1">
          <div className="absolute inset-0 flex flex-col overflow-hidden">
            <TicketsTable tickets={tickets} />
          </div>
        </div>
        <div className="shrink-0">
          <Pagination page={page} pageSize={pageSize} total={total} />
        </div>
      </div>
    </div>
  );
}
