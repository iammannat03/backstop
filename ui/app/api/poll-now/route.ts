import { NextResponse } from "next/server";
import { guardApi } from "@/lib/authz";

// Proxies to ingestion's manual-trigger endpoint, forcing an immediate check
// instead of waiting for the poll interval. Never talk to Zendesk directly
// here, always go through the service that owns the OAuth token manager.
const INGESTION_URL = process.env.INGESTION_URL ?? "http://localhost:8001";

export async function POST() {
  const guard = await guardApi("admin");
  if (guard.response) return guard.response;

  const resp = await fetch(`${INGESTION_URL}/ingest/poll-now`, { method: "POST" });
  const json = await resp.json();
  return NextResponse.json(json, { status: resp.status });
}
