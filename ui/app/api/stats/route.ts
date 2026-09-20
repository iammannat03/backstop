import { NextResponse } from "next/server";
import { guardApi } from "@/lib/authz";
import { getQueueStats } from "@/lib/queries";

export async function GET() {
  const guard = await guardApi("viewer");
  if (guard.response) return guard.response;

  const stats = await getQueueStats();
  return NextResponse.json(stats);
}
