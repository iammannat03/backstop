/**
 * Standalone demo site for the "Backstop is embedded in someone else's
 * product" story, deliberately not part of the Backstop app. It simulates a
 * fictitious SaaS client ("Loopline") whose users hit a normal "Contact
 * support" widget; the request lands in Backstop through the same ingestion
 * endpoint a real ticket would use.
 *
 * A tiny static-file-plus-proxy server on its own port, not a second Next.js
 * app, since the only dynamic behavior needed is one proxied POST.
 */

const PORT = Number(process.env.PORT ?? 3002);
const INGESTION_URL = process.env.INGESTION_URL ?? "http://localhost:8001";

const html = await Bun.file(new URL("./index.html", import.meta.url)).text();

Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);

    if (url.pathname === "/" && req.method === "GET") {
      return new Response(html, { headers: { "Content-Type": "text/html" } });
    }

    if (url.pathname === "/api/submit-ticket" && req.method === "POST") {
      // Proxied server-side: this app never talks to Zendesk itself, only to
      // Backstop's ingestion endpoint, so the shared OAuth token manager
      // stays single-owner.
      const body = await req.text();
      const resp = await fetch(`${INGESTION_URL}/ingest/submit-ticket`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      const text = await resp.text();
      return new Response(text, { status: resp.status, headers: { "Content-Type": "application/json" } });
    }

    return new Response("Not found", { status: 404 });
  },
});

console.log(`Loopline (mock client site) running at http://localhost:${PORT}`);
console.log(`Proxying support requests to Backstop ingestion at ${INGESTION_URL}`);
