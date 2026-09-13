// Mirrors persistence/models.py. Hand-maintained, Python/SQLAlchemy remains
// the schema's source of truth.

export type TicketStatus =
  | "new"
  | "investigating"
  | "proposed_action"
  | "opa_review"
  | "blocked"
  | "verifier_review"
  | "escalated"
  | "executing"
  | "executed"
  | "human_review"
  | "resolved";

export type ActionType =
  | "refund"
  | "partial_refund"
  | "no_action"
  | "escalate"
  | "cancel_subscription"
  | "apply_account_credit"
  | "flag_for_fraud_review";

export interface ProposedAction {
  action_type: ActionType;
  amount: number;
  currency: string;
  target_transaction_id: string | null;
  target_subscription_id: string | null;
  rationale: string;
  confidence: number;
}

export interface Classification {
  is_billing_relevant: boolean;
  intent: string;
  urgency: "low" | "medium" | "high";
  confidence: number;
}

export interface Ticket {
  id: string;
  zendesk_ticket_id: string;
  customer_id: string | null;
  customer_email: string;
  ticket_text: string;
  status: TicketStatus;
  classification: Classification | null;
  stripe_context: Record<string, unknown> | null;
  proposed_action: ProposedAction | null;
  created_at: string;
  updated_at: string;
}

export interface PolicyDecision {
  id: string;
  ticket_id: string;
  decision: "allow" | "deny" | "escalate";
  matched_rule: string | null;
  reason: string | null;
  raw_input: Record<string, unknown> | null;
  raw_output: Record<string, unknown> | null;
  created_at: string;
}

export interface VerificationResult {
  id: string;
  ticket_id: string;
  consistent: boolean;
  mismatch_type: string | null;
  verifier_rationale: string;
  notes: string;
  final_decision: "execute" | "escalate";
  raw_verification_data: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditRecord {
  id: string;
  ticket_id: string;
  event_type: string;
  actor: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface QueueStats {
  inFlight: number;
  autoExecutedToday: number;
  medianCycleSeconds: number | null;
  verifierMismatchRatePct: number | null;
}
