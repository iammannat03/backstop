// Maps audit_records.event_type -> the actor-tier badge shown in the Logs
// view, and picks its color. Mirrors the actors that actually write these
// events across ingestion/worker_agent/governance/verifier_agent/execution.

const EVENT_NAME: Record<string, string> = {
  ticket_ingested: "intake.received",
  classified: "classification.emit",
  stripe_investigated: "tool.call",
  reasoned: "reasoning.emit",
  proposed_action: "action.propose",
  policy_decision: "gate.evaluate",
  blocked: "gate.block",
  cross_referenced: "rederive.start",
  verification_result: "rederive.verdict",
  executed: "execution.commit",
  execution_failed: "execution.fail",
  human_decision: "human.decide",
};

const EVENT_ACTOR: Record<string, string> = {
  ticket_ingested: "ticket",
  classified: "worker",
  stripe_investigated: "worker",
  reasoned: "worker",
  proposed_action: "worker",
  policy_decision: "policy",
  blocked: "policy",
  cross_referenced: "verifier",
  verification_result: "verifier",
  executed: "system",
  execution_failed: "system",
  human_decision: "human",
};

export function eventName(eventType: string): string {
  return EVENT_NAME[eventType] ?? eventType.replace(/_/g, ".");
}

export function eventActor(eventType: string): string {
  return EVENT_ACTOR[eventType] ?? "system";
}

export function eventFlagged(eventType: string, detail: Record<string, unknown> | null): boolean {
  if (eventType === "blocked" || eventType === "execution_failed") return true;
  if (eventType === "verification_result" && detail && detail.consistent === false) return true;
  return false;
}

export function eventSummary(eventType: string, detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  const fn = EVENT_SUMMARY[eventType];
  try {
    return fn ? fn(detail) : JSON.stringify(detail);
  } catch {
    return "";
  }
}

export function eventPayload(eventType: string, detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  const pick = (keys: string[]) =>
    keys
      .filter((k) => detail[k] != null && detail[k] !== "")
      .map((k) => `${k}=${stringifyVal(detail[k])}`)
      .join(" · ");

  switch (eventType) {
    case "ticket_ingested":
      return pick(["zendesk_ticket_id", "customer_email"]);
    case "classified":
      return pick(["intent", "urgency", "confidence"]);
    case "stripe_investigated": {
      const n = Array.isArray(detail.transactions) ? detail.transactions.length : 0;
      return `customer=${detail.customer_id ?? "unresolved"} · ${n} transaction(s)`;
    }
    case "reasoned":
    case "proposed_action":
    case "cross_referenced":
      return pick(["action_type", "amount", "currency", "target_transaction_id", "confidence"]);
    case "policy_decision":
    case "blocked":
      return pick(["decision", "matched_rule"]);
    case "verification_result":
      return pick(["consistent", "mismatch_type", "final_decision"]);
    case "executed":
      return pick(["refund_id", "action_type"]);
    case "execution_failed":
      return pick(["status_code", "reason", "error"]);
    case "human_decision":
      return [pick(["decision"]), decidedByLabel(detail)].filter(Boolean).join(" · ");
    default:
      return "";
  }
}

function decidedByLabel(detail: Record<string, unknown>): string {
  const by = detail.decided_by as { name?: string | null; id?: string; role?: string } | undefined;
  if (!by) return "";
  return `by=${by.name || by.id}${by.role ? ` (${by.role})` : ""}`;
}

function stringifyVal(v: unknown): string {
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

const EVENT_SUMMARY: Record<string, (detail: Record<string, unknown>) => string> = {
  ticket_ingested: (d) => `Zendesk ticket #${d.zendesk_ticket_id} ingested (${d.customer_email}).`,
  classified: (d) =>
    `Classified as ${d.is_billing_relevant ? "billing-relevant" : "not billing-relevant"}: ${d.intent}, ${d.urgency} urgency.`,
  stripe_investigated: (d) =>
    `Pulled Stripe history: ${(d.transactions as unknown[] | undefined)?.length ?? 0} transaction(s), customer ${d.customer_id ?? "unresolved"}.`,
  reasoned: (d) => String(d.rationale ?? ""),
  proposed_action: (d) => `Proposed ${d.action_type}: ${String(d.rationale ?? "")}`,
  policy_decision: (d) => `OPA decision: ${d.decision}${d.matched_rule ? ` (${d.matched_rule})` : ""}.`,
  blocked: (d) => `Blocked: ${d.matched_rule}: ${d.reason}`,
  cross_referenced: (d) => `Verifier independently concluded ${d.action_type}: ${String(d.rationale ?? "")}`,
  verification_result: (d) => String(d.notes ?? ""),
  executed: (d) => (d.refund_id ? `Refund ${d.refund_id} issued.` : `Resolved: ${d.action_type}, no Stripe call needed.`),
  execution_failed: (d) => `Execution failed: ${d.status_code ?? d.reason ?? "error"}`,
  human_decision: (d) => {
    const by = d.decided_by as { name?: string | null; id?: string } | undefined;
    const who = by?.name || by?.id;
    return `${who ? `${who} recorded` : "Human recorded"} decision: ${d.decision}.`;
  },
};
