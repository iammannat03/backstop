export function formatMoney(cents: number, currency = "usd"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
  }).format(cents / 100);
}

export function formatAge(fromIso: string): string {
  const ms = Date.now() - new Date(fromIso).getTime();
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hh = Math.floor(totalSeconds / 3600);
  const mm = Math.floor((totalSeconds % 3600) / 60);
  const ss = totalSeconds % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

import type { ProposedAction } from "./types";

export function isMonetary(actionType: string): boolean {
  return actionType === "refund" || actionType === "partial_refund" || actionType === "apply_account_credit";
}

// A refund/credit action's outcome is a dollar amount; every other
// action_type has no meaningful amount, so describe it by action type instead.
export function describeOutcome(action: ProposedAction): string {
  return isMonetary(action.action_type)
    ? formatMoney(action.amount, action.currency)
    : actionLabel(action.action_type);
}

export function actionLabel(actionType: string): string {
  switch (actionType) {
    case "refund":
      return "Full refund";
    case "partial_refund":
      return "Partial refund";
    case "no_action":
      return "No action needed";
    case "escalate":
      return "Escalate";
    case "cancel_subscription":
      return "Cancel subscription";
    case "apply_account_credit":
      return "Apply account credit";
    case "flag_for_fraud_review":
      return "Flag for fraud review";
    default:
      return actionType;
  }
}
