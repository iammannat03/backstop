import { NextResponse } from "next/server";
import { guardApi } from "@/lib/authz";
import { getTicket, getTicketDetail, recordHumanDecision } from "@/lib/queries";
import type { ProposedAction } from "@/lib/types";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const guard = await guardApi("viewer");
  if (guard.response) return guard.response;

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

const DECISIONS = ["approve_verifier", "approve_worker", "hold"] as const;

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const guard = await guardApi("approver");
  if (guard.response) return guard.response;
  const { viewer } = guard;

  const { id } = await params;

  let body: Partial<DecisionBody> | null;
  try {
    body = (await request.json()) as Partial<DecisionBody> | null;
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  const decision = body?.decision;
  const chosenAction = body?.chosenAction ?? null;
  if (!decision || !DECISIONS.includes(decision)) {
    return NextResponse.json({ error: "invalid decision" }, { status: 400 });
  }
  if (decision !== "hold" && (typeof chosenAction !== "object" || chosenAction === null)) {
    return NextResponse.json({ error: "chosenAction is required for this decision" }, { status: 400 });
  }
  if (!(await getTicket(id))) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }

  const result = await recordHumanDecision(id, decision, chosenAction, {
    id: viewer.id,
    name: viewer.name,
    role: viewer.role,
  });
  return NextResponse.json(result);
}
