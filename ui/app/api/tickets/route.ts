import { NextResponse } from "next/server";
import { getTickets } from "@/lib/queries";
import type { ActionType } from "@/lib/types";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const status = searchParams.get("status") as "all" | "needs_review" | "in_progress" | "resolved" | null;
  const actionType = searchParams.get("type") as ActionType | "all" | null;
  const page = searchParams.get("page");

  const result = await getTickets({
    q: searchParams.get("q") ?? undefined,
    customerId: searchParams.get("customerId") ?? undefined,
    status: status ?? undefined,
    actionType: actionType ?? undefined,
    dateFrom: searchParams.get("dateFrom") ?? undefined,
    dateTo: searchParams.get("dateTo") ?? undefined,
    page: page ? Number(page) : undefined,
  });

  return NextResponse.json(result);
}
