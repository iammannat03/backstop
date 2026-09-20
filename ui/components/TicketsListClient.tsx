"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
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

  // Polling URL tracks the current filters/page, so changing a filter
  // naturally starts polling the newly filtered endpoint.
  const [{ tickets, total, page, pageSize }, refetchTickets] = usePolling<ListData>(
    `/api/tickets${query ? `?${query}` : ""}`,
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
          <div className="flex flex-wrap items-center gap-2.5 border-b border-divider bg-neutral-100 px-[22px] py-2.5">
            <button disabled={syncing} onClick={handleRefresh} className="btn btn-secondary text-[12px]">
              {syncing ? "Syncing…" : "↻ Refresh"}
            </button>
            <span className="font-data text-[11px] text-neutral-600">
              {syncResult ?? "Pulls new tickets from Zendesk right now instead of waiting for the poller"}
            </span>
          </div>
        )}
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
        <div className="min-h-0 flex-1 overflow-hidden">
          <TicketsTable tickets={tickets} />
        </div>
        <Pagination page={page} pageSize={pageSize} total={total} />
      </div>
    </div>
  );
}
