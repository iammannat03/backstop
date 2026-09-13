import { TicketDetailClient } from "@/components/TicketDetailClient";
import { getTicketDetail } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function TicketDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await getTicketDetail(id);

  // Built server-side so ZENDESK_SUBDOMAIN stays out of the client bundle.
  const zendeskSubdomain = process.env.ZENDESK_SUBDOMAIN ?? null;

  return <TicketDetailClient detail={detail} zendeskSubdomain={zendeskSubdomain} />;
}
