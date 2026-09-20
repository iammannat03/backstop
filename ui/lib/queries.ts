import { QueryCommand, GetCommand, PutCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { ALL_TICKETS_INDEX, ENTITY_DATE_INDEX, STATUS_INDEX, TABLE_NAME, doc } from "./db";
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

type Item = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const NEEDS_REVIEW_STATUSES = ["escalated", "blocked"];
const TERMINAL_OR_HOLD_STATUSES = ["resolved", "escalated", "blocked"];

// Same shape persistence/dynamo.py writes: ISO 8601 with microseconds and a
// +00:00 offset. Keys sort lexicographically, so writes from this process must
// use the identical format or ordering against Python-written items breaks.
function nowIso(date: Date = new Date()): string {
  return date.toISOString().replace("Z", "000+00:00");
}

// Epoch microseconds. Date only keeps milliseconds, and the stored timestamps
// carry microsecond precision that the cycle-time median should not lose.
function epochMicros(iso: string): number {
  const ms = new Date(iso).getTime();
  const frac = /\.(\d+)/.exec(iso)?.[1] ?? "";
  const micros = Number(frac.padEnd(6, "0").slice(3, 6));
  return ms * 1000 + micros;
}

function ticketFromItem(item: Item): Ticket {
  return {
    id: item.id,
    zendesk_ticket_id: item.zendesk_ticket_id,
    customer_id: item.customer_id ?? null,
    customer_email: item.customer_email,
    ticket_text: item.ticket_text,
    status: item.status,
    classification: item.classification ?? null,
    stripe_context: item.stripe_context ?? null,
    proposed_action: item.proposed_action ?? null,
    action_type: item.action_type ?? null,
    slack_channel: item.slack_channel ?? null,
    slack_thread_ts: item.slack_thread_ts ?? null,
    created_at: item.created_at,
    updated_at: item.updated_at,
  };
}

function policyFromItem(item: Item): PolicyDecision {
  return {
    id: item.id,
    ticket_id: item.ticket_id,
    decision: item.decision,
    matched_rule: item.matched_rule ?? null,
    reason: item.reason ?? null,
    raw_input: item.raw_input ?? null,
    raw_output: item.raw_output ?? null,
    created_at: item.created_at,
  };
}

function verificationFromItem(item: Item): VerificationResult {
  return {
    id: item.id,
    ticket_id: item.ticket_id,
    consistent: item.consistent,
    mismatch_type: item.mismatch_type ?? null,
    verifier_rationale: item.verifier_rationale,
    notes: item.notes,
    final_decision: item.final_decision,
    raw_verification_data: item.raw_verification_data ?? null,
    created_at: item.created_at,
  };
}

function auditFromItem(item: Item): AuditRecord {
  return {
    id: item.id,
    ticket_id: item.ticket_id,
    event_type: item.event_type,
    actor: item.actor,
    detail: item.detail ?? null,
    created_at: item.created_at,
  };
}

// Runs a Query to exhaustion, following LastEvaluatedKey.
async function queryAll(params: Omit<ConstructorParameters<typeof QueryCommand>[0], "TableName">): Promise<Item[]> {
  const items: Item[] = [];
  let startKey: Record<string, unknown> | undefined;
  do {
    const res = await doc.send(new QueryCommand({ TableName: TABLE_NAME, ...params, ExclusiveStartKey: startKey }));
    items.push(...(res.Items ?? []));
    startKey = res.LastEvaluatedKey;
  } while (startKey);
  return items;
}

function queryStatusIndex(status: string, skGte?: string): Promise<Item[]> {
  return queryAll({
    IndexName: STATUS_INDEX,
    KeyConditionExpression: skGte ? "GSI1PK = :pk AND GSI1SK >= :sk" : "GSI1PK = :pk",
    ExpressionAttributeValues: skGte ? { ":pk": `STATUS#${status}`, ":sk": skGte } : { ":pk": `STATUS#${status}` },
    ScanIndexForward: false,
  });
}

function queryAllTickets(): Promise<Item[]> {
  return queryAll({
    IndexName: ALL_TICKETS_INDEX,
    KeyConditionExpression: "GSI2PK = :pk",
    ExpressionAttributeValues: { ":pk": "TICKET" },
    ScanIndexForward: false,
  });
}

// The single ticket-listing query behind the Tickets page. "Escalation" in
// the nav is just this same query with status=needs_review.
//
// Status uses a key condition on StatusIndex (resolved, needs_review) or the
// AllTicketsIndex (all, in_progress, which is defined by exclusion). The
// remaining filters run in code over the result so the total stays exact.
export async function getTickets(filters: TicketListFilters = {}): Promise<TicketListResult> {
  const { q, customerId, status = "all", actionType = "all", dateFrom, dateTo } = filters;
  const requestedPage = Math.max(1, filters.page ?? 1);
  const pageSize = filters.pageSize ?? 8;

  let rawItems: Item[];
  if (status === "resolved") {
    rawItems = await queryStatusIndex("resolved");
  } else if (status === "needs_review") {
    const groups = await Promise.all(NEEDS_REVIEW_STATUSES.map((s) => queryStatusIndex(s)));
    rawItems = groups.flat().sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
  } else {
    rawItems = await queryAllTickets();
    if (status === "in_progress") {
      rawItems = rawItems.filter((i) => !TERMINAL_OR_HOLD_STATUSES.includes(i.status));
    }
  }

  let tickets = rawItems.map(ticketFromItem);

  if (actionType !== "all") {
    tickets = tickets.filter((t) => t.action_type === actionType);
  }
  if (q) {
    const needle = q.toLowerCase();
    tickets = tickets.filter(
      (t) => (t.ticket_text ?? "").toLowerCase().includes(needle) || (t.customer_email ?? "").toLowerCase().includes(needle),
    );
  }
  if (customerId) {
    const needle = customerId.toLowerCase();
    tickets = tickets.filter((t) => t.customer_id && t.customer_id.toLowerCase().includes(needle));
  }
  if (dateFrom) {
    tickets = tickets.filter((t) => t.created_at >= dateFrom);
  }
  if (dateTo) {
    // created_at < dateTo + 1 day, so the end date is inclusive.
    const end = new Date(`${dateTo}T00:00:00Z`);
    if (!Number.isNaN(end.getTime())) {
      end.setUTCDate(end.getUTCDate() + 1);
      const upper = end.toISOString().slice(0, 19);
      tickets = tickets.filter((t) => t.created_at < upper);
    }
  }

  // The page size can change with the window height, so a page that no
  // longer exists falls back to the last one instead of coming back empty.
  const page = Math.min(requestedPage, Math.max(1, Math.ceil(tickets.length / pageSize)));
  const start = (page - 1) * pageSize;
  const pageTickets = await Promise.all(
    tickets.slice(start, start + pageSize).map(async (t) => {
      if (t.status !== "escalated") return t;
      const policy = await getLatestPolicyDecision(t.id);
      return policy && policy.decision !== "allow" ? { ...t, policy_blocked: true } : t;
    }),
  );
  return { tickets: pageTickets, total: tickets.length, page, pageSize };
}

export async function getQueueStats(): Promise<QueueStats> {
  const cutoff = nowIso(new Date(Date.now() - 24 * 60 * 60 * 1000));

  const allTickets = await queryAllTickets();
  const inFlight = allTickets.filter((t) => t.status !== "resolved" && t.status !== "escalated").length;

  const [resolvedRecent, escalatedRecent] = await Promise.all([
    queryStatusIndex("resolved", cutoff),
    queryStatusIndex("escalated", cutoff),
  ]);

  const cycleSeconds = [...resolvedRecent, ...escalatedRecent]
    .map((i) => (epochMicros(i.updated_at) - epochMicros(i.created_at)) / 1e6)
    .sort((a, b) => a - b);
  let medianCycleSeconds: number | null = null;
  if (cycleSeconds.length) {
    const mid = Math.floor(cycleSeconds.length / 2);
    medianCycleSeconds =
      cycleSeconds.length % 2 ? cycleSeconds[mid] : (cycleSeconds[mid - 1] + cycleSeconds[mid]) / 2;
  }

  const verifications = await queryAll({
    IndexName: ENTITY_DATE_INDEX,
    KeyConditionExpression: "GSI4PK = :pk AND GSI4SK >= :sk",
    ExpressionAttributeValues: { ":pk": "verification_result", ":sk": cutoff },
  });
  const mismatched = verifications.filter((v) => v.consistent === false).length;

  return {
    inFlight,
    autoExecutedToday: resolvedRecent.length,
    medianCycleSeconds,
    verifierMismatchRatePct: verifications.length > 0 ? (mismatched / verifications.length) * 100 : null,
  };
}

export async function getTicket(id: string): Promise<Ticket | null> {
  const res = await doc.send(new GetCommand({ TableName: TABLE_NAME, Key: { PK: `TICKET#${id}`, SK: "METADATA" } }));
  return res.Item ? ticketFromItem(res.Item) : null;
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
  // The verifier's own ProposedAction isn't stored on the verification result,
  // it's captured in full on the "cross_referenced" audit event.
  const crossReferenced = [...auditTrail].reverse().find((e) => e.event_type === "cross_referenced");
  const verifierAction = (crossReferenced?.detail as unknown as ProposedAction) ?? null;

  if (ticket.status === "escalated" && policyDecision && policyDecision.decision !== "allow") {
    ticket.policy_blocked = true;
  }

  return { ticket, policyDecision, verificationResult, verifierAction, auditTrail };
}

async function latestBySortKeyPrefix(ticketId: string, prefix: string): Promise<Item | null> {
  const res = await doc.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "PK = :pk AND begins_with(SK, :prefix)",
      ExpressionAttributeValues: { ":pk": `TICKET#${ticketId}`, ":prefix": prefix },
      ScanIndexForward: false,
      Limit: 1,
    }),
  );
  return res.Items?.[0] ?? null;
}

export async function getLatestPolicyDecision(ticketId: string): Promise<PolicyDecision | null> {
  const item = await latestBySortKeyPrefix(ticketId, "POLICY#");
  return item ? policyFromItem(item) : null;
}

export async function getLatestVerificationResult(ticketId: string): Promise<VerificationResult | null> {
  const item = await latestBySortKeyPrefix(ticketId, "VERIFICATION#");
  return item ? verificationFromItem(item) : null;
}

async function insertAuditRecord(
  ticketId: string,
  eventType: string,
  actor: string,
  detail: Record<string, unknown>,
): Promise<void> {
  const id = crypto.randomUUID();
  const now = nowIso();
  await doc.send(
    new PutCommand({
      TableName: TABLE_NAME,
      Item: {
        PK: `TICKET#${ticketId}`,
        SK: `AUDIT#${now}#${id}`,
        entity_type: "audit_record",
        id,
        ticket_id: ticketId,
        event_type: eventType,
        actor,
        detail,
        created_at: now,
        GSI4PK: "audit_record",
        GSI4SK: `${now}#${id}`,
      },
    }),
  );
}

export async function getAuditTrail(ticketId: string): Promise<AuditRecord[]> {
  const items = await queryAll({
    KeyConditionExpression: "PK = :pk AND begins_with(SK, :prefix)",
    ExpressionAttributeValues: { ":pk": `TICKET#${ticketId}`, ":prefix": "AUDIT#" },
    ScanIndexForward: true,
  });
  return items.map(auditFromItem);
}

/**
 * A human approving or overriding a flagged action. "hold" just logs the
 * decision and leaves the ticket escalated. "approve_worker" / "approve_verifier"
 * execute the chosen ProposedAction directly (DynamoDB and Stripe, right
 * here), then notifyHumanDecision() below hands off to the Python ingestion
 * service for the actual Slack post and Zendesk write-back, since that
 * process is the only one that should own the shared OAuth token manager.
 */
export async function recordHumanDecision(
  ticketId: string,
  decision: "approve_verifier" | "approve_worker" | "hold",
  chosenAction: ProposedAction | null,
  decidedBy: { id: string; name: string | null; role: string },
): Promise<{ status: string; refundId?: string; error?: string }> {
  await insertAuditRecord(ticketId, "human_decision", "human", {
    decision,
    chosenAction,
    decided_by: { id: decidedBy.id, name: decidedBy.name, role: decidedBy.role },
  });

  if (decision === "hold" || !chosenAction) {
    return { status: "escalated" };
  }

  // Also refreshes action_type and the status GSI keys, the list filters and
  // the queue stats read those, not the proposed_action blob.
  async function markResolved(): Promise<void> {
    const now = nowIso();
    await doc.send(
      new UpdateCommand({
        TableName: TABLE_NAME,
        Key: { PK: `TICKET#${ticketId}`, SK: "METADATA" },
        UpdateExpression:
          "SET #status = :status, proposed_action = :pa, action_type = :at, updated_at = :now, GSI1PK = :g1pk, GSI1SK = :g1sk, GSI2SK = :g2sk",
        ExpressionAttributeNames: { "#status": "status" },
        ExpressionAttributeValues: {
          ":status": "resolved",
          ":pa": chosenAction,
          ":at": chosenAction!.action_type,
          ":now": now,
          ":g1pk": "STATUS#resolved",
          ":g1sk": `${now}#${ticketId}`,
          ":g2sk": `${now}#${ticketId}`,
        },
      }),
    );
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
