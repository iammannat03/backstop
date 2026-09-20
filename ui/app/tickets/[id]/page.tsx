import { TicketDetailClient } from "@/components/TicketDetailClient";
import { requirePageViewer } from "@/lib/authz";
import { hasRole } from "@/lib/roles";
import { getTicketDetail } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function TicketDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const viewer = await requirePageViewer(`/tickets/${encodeURIComponent(id)}`);
  const detail = await getTicketDetail(id);

  // Built server-side so ZENDESK_SUBDOMAIN stays out of the client bundle.
  const zendeskSubdomain = process.env.ZENDESK_SUBDOMAIN ?? null;

  return <TicketDetailClient detail={detail} zendeskSubdomain={zendeskSubdomain}
      canDecide={hasRole(viewer.role, "approver")}
    />;
}
