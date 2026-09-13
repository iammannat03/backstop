import type postgres from "postgres";
import { sql } from "./db";
import { applyAccountCredit, cancelSubscription, issueRefund } from "./stripe";
import type {
  ActionType,
  AuditRecord,
  PolicyDecision,
  ProposedAction,
  QueueStats,
  Ticket,
  VerificationResult,
} from "./types";

export interface TicketListFilters {
  q?: string;
  customerId?: string;
  status?: "all" | "needs_review" | "in_progress" | "resolved";
  actionType?: ActionType | "all";
  dateFrom?: string;
  dateTo?: string;
  page?: number;
  pageSize?: number;
}

export interface TicketListResult {
  tickets: Ticket[];
  total: number;
  page: number;
  pageSize: number;
}

// The single ticket-listing query behind the Tickets page. "Escalation" in
// the nav is just this same query with status=needs_review.
export async function getTickets(filters: TicketListFilters = {}): Promise<TicketListResult> {
  const { q, customerId, status = "all", actionType = "all", dateFrom, dateTo } = filters;
  const page = Math.max(1, filters.page ?? 1);
  const pageSize = filters.pageSize ?? 25;

  // needs_review mirrors isHold()'s escalated+blocked. in_progress excludes
  // both terminal and hold states, i.e. still actively moving.
  const statusClause =
    status === "needs_review"
      ? sql`status IN ('escalated', 'blocked')`
      : status === "in_progress"
        ? sql`status NOT IN ('resolved', 'escalated', 'blocked')`
        : status === "resolved"
          ? sql`status = 'resolved'`
          : sql`TRUE`;

  const actionTypeClause =
    actionType === "all" ? sql`TRUE` : sql`proposed_action ->> 'action_type' = ${actionType}`;

  const searchClause = q
    ? sql`(ticket_text ILIKE ${"%" + q + "%"} OR customer_email ILIKE ${"%" + q + "%"})`
    : sql`TRUE`;

  const customerIdClause = customerId ? sql`customer_id ILIKE ${"%" + customerId + "%"}` : sql`TRUE`;

  const dateFromClause = dateFrom ? sql`created_at >= ${dateFrom}` : sql`TRUE`;
  const dateToClause = dateTo ? sql`created_at < (${dateTo}::date + interval '1 day')` : sql`TRUE`;

  const whereClause = sql`${statusClause} AND ${actionTypeClause} AND ${searchClause} AND ${customerIdClause} AND ${dateFromClause} AND ${dateToClause}`;

  const [tickets, [{ count }]] = await Promise.all([
    sql<Ticket[]>`
      SELECT * FROM tickets
      WHERE ${whereClause}
      ORDER BY updated_at DESC
      LIMIT ${pageSize} OFFSET ${(page - 1) * pageSize}
    `,
    sql<{ count: string }[]>`SELECT count(*) FROM tickets WHERE ${whereClause}`,
  ]);

  return { tickets, total: Number(count), page, pageSize };
}

export async function getQueueStats(): Promise<QueueStats> {
  const [{ in_flight }] = await sql<{ in_flight: string }[]>`
    SELECT count(*) AS in_flight FROM tickets
    WHERE status NOT IN ('resolved', 'escalated')
  `;

  const [{ auto_executed }] = await sql<{ auto_executed: string }[]>`
    SELECT count(*) AS auto_executed FROM tickets
    WHERE status = 'resolved' AND updated_at >= now() - interval '24 hours'
  `;

  const [{ median_seconds }] = await sql<{ median_seconds: number | null }[]>`
    SELECT percentile_cont(0.5) WITHIN GROUP (
      ORDER BY EXTRACT(EPOCH FROM (updated_at - created_at))
    ) AS median_seconds
    FROM tickets
    WHERE status IN ('resolved', 'escalated') AND updated_at >= now() - interval '24 hours'
  `;

  const [{ total, mismatched }] = await sql<{ total: string; mismatched: string }[]>`
    SELECT count(*) AS total, count(*) FILTER (WHERE consistent = false) AS mismatched
    FROM verification_results
    WHERE created_at >= now() - interval '24 hours'
  `;

  const totalNum = Number(total);
  return {
    inFlight: Number(in_flight),
    autoExecutedToday: Number(auto_executed),
    medianCycleSeconds: median_seconds,
    verifierMismatchRatePct: totalNum > 0 ? (Number(mismatched) / totalNum) * 100 : null,
  };
}

export async function getTicket(id: string): Promise<Ticket | null> {
  const rows = await sql<Ticket[]>`SELECT * FROM tickets WHERE id = ${id}`;
  return rows[0] ?? null;
}

export interface TicketDetail {
  ticket: Ticket;
  policyDecision: PolicyDecision | null;
  verificationResult: VerificationResult | null;
  verifierAction: ProposedAction | null;
  auditTrail: AuditRecord[];
}

// Backs the entire /tickets/[id] detail page, both the reasoning comparison
// and the audit-trail timeline are derived from this one fetch.
export async function getTicketDetail(ticketId: string): Promise<TicketDetail | null> {
  const ticket = await getTicket(ticketId);
  if (!ticket) return null;
  const [policyDecision, verificationResult, auditTrail] = await Promise.all([
    getLatestPolicyDecision(ticketId),
    getLatestVerificationResult(ticketId),
    getAuditTrail(ticketId),
  ]);
  // The verifier's own ProposedAction isn't a column on verification_results,
  // it's captured in full on the "cross_referenced" audit event.
  const crossReferenced = [...auditTrail].reverse().find((e) => e.event_type === "cross_referenced");
  const verifierAction = (crossReferenced?.detail as unknown as ProposedAction) ?? null;

  return { ticket, policyDecision, verificationResult, verifierAction, auditTrail };
}

export async function getLatestPolicyDecision(ticketId: string): Promise<PolicyDecision | null> {
  const rows = await sql<PolicyDecision[]>`
    SELECT * FROM policy_decisions WHERE ticket_id = ${ticketId}
    ORDER BY created_at DESC LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function getLatestVerificationResult(ticketId: string): Promise<VerificationResult | null> {
  const rows = await sql<VerificationResult[]>`
    SELECT * FROM verification_results WHERE ticket_id = ${ticketId}
    ORDER BY created_at DESC LIMIT 1
  `;
  return rows[0] ?? null;
}

/**
 * audit_records.id has no database-level DEFAULT (SQLAlchemy generates it
 * Python-side), so a raw INSERT from this Node process must supply its own id.
 */
async function insertAuditRecord(
  ticketId: string,
  eventType: string,
  actor: string,
  detail: Record<string, unknown>,
): Promise<void> {
  await sql`
    INSERT INTO audit_records (id, ticket_id, event_type, actor, detail)
    VALUES (${crypto.randomUUID()}, ${ticketId}, ${eventType}, ${actor}, ${sql.json(detail as unknown as postgres.JSONValue)})
  `;
}

export async function getAuditTrail(ticketId: string): Promise<AuditRecord[]> {
  return sql<AuditRecord[]>`
    SELECT * FROM audit_records WHERE ticket_id = ${ticketId}
    ORDER BY created_at ASC
  `;
}

/**
 * A human approving or overriding a flagged action. "hold" just logs the
 * decision and leaves the ticket escalated. "approve_worker" / "approve_verifier"
 * execute the chosen ProposedAction directly and move the ticket to a
 * terminal status. Deliberately doesn't write back to Zendesk or Slack,
 * unlike the automated path.
 */
export async function recordHumanDecision(
  ticketId: string,
  decision: "approve_verifier" | "approve_worker" | "hold",
  chosenAction: ProposedAction | null,
): Promise<{ status: string; refundId?: string; error?: string }> {
  await insertAuditRecord(ticketId, "human_decision", "human", { decision, chosenAction });

  if (decision === "hold" || !chosenAction) {
    return { status: "escalated" };
  }

  async function markResolved(): Promise<void> {
    await sql`
      UPDATE tickets
      SET status = 'resolved', proposed_action = ${sql.json(chosenAction as unknown as postgres.JSONValue)}, updated_at = now()
      WHERE id = ${ticketId}
    `;
  }

  if (chosenAction.action_type === "refund" || chosenAction.action_type === "partial_refund") {
    if (!chosenAction.target_transaction_id) {
      return { status: "escalated", error: "No target transaction on the chosen action" };
    }
    try {
      const refund = await issueRefund(ticketId, chosenAction.target_transaction_id, chosenAction.amount);
      await markResolved();
      await insertAuditRecord(ticketId, "executed", "human", { refund_id: refund.id, ...chosenAction });
      await notifyHumanDecision(ticketId, { outcome: "resolved", action: chosenAction, refundId: refund.id });
      return { status: "resolved", refundId: refund.id };
    } catch (err) {
      await insertAuditRecord(ticketId, "execution_failed", "human", { error: String(err) });
      await notifyHumanDecision(ticketId, { outcome: "failed", errorDetail: String(err) });
      return { status: "escalated", error: String(err) };
    }
  }

  if (chosenAction.action_type === "cancel_subscription") {
    if (!chosenAction.target_subscription_id) {
      return { status: "escalated", error: "No target subscription on the chosen action" };
    }
    try {
      await cancelSubscription(ticketId, chosenAction.target_subscription_id);
      await markResolved();
      await insertAuditRecord(ticketId, "executed", "human", { ...chosenAction });
      await notifyHumanDecision(ticketId, { outcome: "resolved", action: chosenAction });
      return { status: "resolved" };
    } catch (err) {
      await insertAuditRecord(ticketId, "execution_failed", "human", { error: String(err) });
      await notifyHumanDecision(ticketId, { outcome: "failed", errorDetail: String(err) });
      return { status: "escalated", error: String(err) };
    }
  }

  if (chosenAction.action_type === "apply_account_credit") {
    const ticket = await getTicket(ticketId);
    if (!ticket?.customer_id) {
      return { status: "escalated", error: "No known Stripe customer to credit" };
    }
    try {
      await applyAccountCredit(ticketId, ticket.customer_id, chosenAction.amount, chosenAction.currency);
      await markResolved();
      await insertAuditRecord(ticketId, "executed", "human", { ...chosenAction });
      await notifyHumanDecision(ticketId, { outcome: "resolved", action: chosenAction });
      return { status: "resolved" };
    } catch (err) {
      await insertAuditRecord(ticketId, "execution_failed", "human", { error: String(err) });
      await notifyHumanDecision(ticketId, { outcome: "failed", errorDetail: String(err) });
      return { status: "escalated", error: String(err) };
    }
  }

  // no_action / escalate / flag_for_fraud_review: the human already is the
  // reviewer at this point, nothing left to move.
  await markResolved();
  await notifyHumanDecision(ticketId, { outcome: "resolved", action: chosenAction });
  return { status: "resolved" };
}

// Calls into the Python ingestion service so the actual Slack post and
// Zendesk write-back go through the one process that owns the shared OAuth
// token manager. This UI process never talks to Zendesk directly.
const INGESTION_URL = process.env.INGESTION_URL ?? "http://localhost:8001";

async function notifyHumanDecision(
  ticketId: string,
  params: { outcome: "resolved" | "failed"; action?: ProposedAction; refundId?: string; errorDetail?: string },
): Promise<void> {
  try {
    const res = await fetch(`${INGESTION_URL}/ingest/human-decision-notify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticket_id: ticketId,
        outcome: params.outcome,
        action: params.action ?? null,
        refund_id: params.refundId ?? null,
        error_detail: params.errorDetail ?? null,
      }),
    });
    if (!res.ok) {
      console.error("human-decision-notify failed:", res.status, await res.text());
    }
  } catch (err) {
    console.error("Could not reach ingestion service for human-decision-notify:", err);
  }
}
