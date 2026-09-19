import { NextResponse } from "next/server";
import { getTicketDetail, recordHumanDecision } from "@/lib/queries";
import type { ProposedAction } from "@/lib/types";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await getTicketDetail(id);
  if (!detail) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  return NextResponse.json(detail);
}

interface DecisionBody {
  decision: "approve_verifier" | "approve_worker" | "hold";
  chosenAction: ProposedAction | null;
}

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await request.json()) as DecisionBody;
  const result = await recordHumanDecision(id, body.decision, body.chosenAction);
  return NextResponse.json(result);
}
