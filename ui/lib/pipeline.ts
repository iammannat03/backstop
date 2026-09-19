import type { TicketStatus } from "./types";

// Fixed visual stages the pipeline track renders as segments, collapsing the
// full TicketStatus enum down to the five stages worth showing at a glance.
export const PIPELINE_STAGES = [
  "investigating",
  "proposed",
  "policy_check",
  "verifying",
  "executed",
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

const STATUS_TO_STAGE_INDEX: Record<TicketStatus, number> = {
  new: 0,
  investigating: 0,
  proposed_action: 1,
  opa_review: 2,
  blocked: 2,
  verifier_review: 3,
  escalated: 3,
  executing: 4,
  executed: 4,
  human_review: 4,
  resolved: 4,
};

const STATUS_LABEL: Record<TicketStatus, string> = {
  new: "Investigating…",
  investigating: "Investigating…",
  proposed_action: "Proposed…",
  opa_review: "Policy check…",
  blocked: "Blocked",
  verifier_review: "Verifying…",
  escalated: "Verifier hold",
  executing: "Executing…",
  executed: "Executed",
  human_review: "Human review",
  resolved: "Executed",
};

// A policy block leaves the ticket in "escalated" (same as a verifier hold),
// so callers pass the derived flag to tell the two apart.
export function stageIndexFor(status: TicketStatus, policyBlocked = false): number {
  if (policyBlocked && status === "escalated") return STATUS_TO_STAGE_INDEX.blocked;
  return STATUS_TO_STAGE_INDEX[status] ?? 0;
}

export function statusLabel(status: TicketStatus, policyBlocked = false): string {
  if (policyBlocked && status === "escalated") return "Policy block";
  return STATUS_LABEL[status] ?? status;
}

export function isHold(status: TicketStatus): boolean {
  return status === "escalated" || status === "blocked";
}

export function isDone(status: TicketStatus): boolean {
  return status === "resolved" || status === "executed";
}
