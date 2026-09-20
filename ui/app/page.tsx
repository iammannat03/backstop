import { requirePageViewer } from "@/lib/authz";
import { hasRole } from "@/lib/roles";
import { TicketsListClient } from "@/components/TicketsListClient";
import { getQueueStats, getTickets } from "@/lib/queries";
import type { ActionType } from "@/lib/types";

// Without this, a Server Component with no dynamic API usage gets statically
// prerendered at build time, freezing this live dashboard on stale data.
export const dynamic = "force-dynamic";

export default async function TicketsPage({
  searchParams,
}: {
  searchParams: Promise<{
    q?: string;
    customerId?: string;
    status?: string;
    type?: string;
    dateFrom?: string;
    dateTo?: string;
    page?: string;
  }>;
}) {
  const params = await searchParams;
  const qs = new URLSearchParams(
    Object.entries(params).filter((e): e is [string, string] => typeof e[1] === "string"),
  ).toString();
  const viewer = await requirePageViewer(qs ? `/?${qs}` : "/");
  const status = params.status as "all" | "needs_review" | "in_progress" | "resolved" | undefined;

  const [stats, list] = await Promise.all([
    getQueueStats(),
    getTickets({
      q: params.q,
      customerId: params.customerId,
      status,
      actionType: params.type as ActionType | "all" | undefined,
      dateFrom: params.dateFrom,
      dateTo: params.dateTo,
      page: params.page ? Number(params.page) : undefined,
    }),
  ]);

  return <TicketsListClient initial={list} initialStats={stats} canSync={hasRole(viewer.role, "admin")} />;
}
