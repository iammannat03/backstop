import { NextResponse } from "next/server";
import { guardApi } from "@/lib/authz";
import { getTickets } from "@/lib/queries";
import type { ActionType } from "@/lib/types";

export async function GET(request: Request) {
  const guard = await guardApi("viewer");
  if (guard.response) return guard.response;

  const { searchParams } = new URL(request.url);
  const status = searchParams.get("status") as "all" | "needs_review" | "in_progress" | "resolved" | null;
  const actionType = searchParams.get("type") as ActionType | "all" | null;
  const page = searchParams.get("page");
  const pageSizeParam = Number(searchParams.get("pageSize"));
  const pageSize = Number.isInteger(pageSizeParam) && pageSizeParam >= 3 && pageSizeParam <= 25 ? pageSizeParam : undefined;

  const result = await getTickets({
    q: searchParams.get("q") ?? undefined,
    customerId: searchParams.get("customerId") ?? undefined,
    status: status ?? undefined,
    actionType: actionType ?? undefined,
    dateFrom: searchParams.get("dateFrom") ?? undefined,
    dateTo: searchParams.get("dateTo") ?? undefined,
    page: page ? Number(page) : undefined,
    pageSize,
  });

  return NextResponse.json(result);
}
