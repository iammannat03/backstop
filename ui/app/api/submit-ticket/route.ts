// Demo-only proxy for the Loopline support widget. The widget never talks to
// Zendesk itself, it posts here and this forwards to the ingestion API, which
// owns the Zendesk OAuth token.
const INGESTION_URL = process.env.INGESTION_URL ?? "http://localhost:8001";

export async function POST(req: Request) {
  const resp = await fetch(`${INGESTION_URL}/ingest/submit-ticket`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await req.text(),
  });
  return new Response(await resp.text(), {
    status: resp.status,
    headers: { "Content-Type": "application/json" },
  });
}
