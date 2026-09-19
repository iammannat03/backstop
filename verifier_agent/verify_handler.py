"""Verifier Lambda handler. Independently re-derives a conclusion for a
ticket, then checks it against the worker's proposed action.

Ported as one combined handler rather than two (cross_reference then
consistency_checker) because backstop-prac's pipeline.py always ran them
back to back with no branch in between, splitting them into two Lambda
invocations would only add a state transition without giving the Step
Functions state machine anywhere useful to branch between them.

The independence boundary that must never be broken: _derive_independent_
conclusion() below is the only place that calls the model for this
handler, and it takes only ticket_text, a freshly re-pulled StripeHistory,
and the customer's recent ticket count, nothing from the worker's stored
proposed_action ever reaches it. The ticket's proposed_action is read from
DynamoDB only after that call has already returned, purely to compare
structured fields against the verifier's own independent conclusion in
_check_consistency(). Do not reorder this, and do not add proposed_action
(or its rationale) as an input to _derive_independent_conclusion.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from persistence import dynamo
from shared.action_types import ACTION_TYPE_VALUES
from shared.models import ProposedAction, StripeHistory, VerificationResult
from worker_agent.bedrock_client import VERIFIER_MODEL, generate_json
from worker_agent.stripe_investigator import investigate_customer

logger = logging.getLogger("verifier_agent.verify_handler")

# Matches governance's escalation.ambiguous_intent_confidence_threshold.
_LOW_CONFIDENCE_THRESHOLD = 0.6

_SCHEMA = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": ACTION_TYPE_VALUES},
        "amount": {"type": "integer"},
        "currency": {"type": "string"},
        "target_transaction_id": {"type": "string"},
        "target_subscription_id": {"type": "string"},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "action_type",
        "amount",
        "currency",
        "target_transaction_id",
        "target_subscription_id",
        "rationale",
        "confidence",
    ],
}

_SYSTEM_INSTRUCTION = """You are an independent verification auditor for a billing support pipeline. You have \
NOT seen any other agent's conclusion about this ticket, and you will not be shown one. You are forming \
your own conclusion from scratch, using only the raw ticket text, this customer's real Stripe billing \
history (freshly re-pulled for this review), and their recent support ticket pattern. Assume nothing about \
what any prior review might have found; verify everything against the data you were given here.

Reconcile what the customer is asking for against what actually happened in Stripe. Do not trust the \
customer's framing at face value.

- Check first, before any other rule below: if the ticket describes an unauthorized charge, a \
stolen/compromised card, or a suspected account takeover, propose "flag_for_fraud_review", never "escalate", \
never a refund or credit, regardless of whether Stripe billing history exists or not. This is a categorical \
safety signal, not a data-sufficiency question. "I don't have enough billing history" is not a reason to \
fall back to plain "escalate" when the ticket is actually describing a fraud/theft signal.
- A transaction with is_proration=true may be a legitimate charge even if it looks like an unexpected or \
duplicate charge to the customer. Read proration_details to understand why it happened before concluding \
it's an error.
- Only propose a refund against a target_transaction_id that actually appears in the provided transactions \
list. Never invent one.
- Never propose a refund amount larger than that transaction's own remaining refundable amount \
(amount - amount_refunded).
- When more than one charge could satisfy the same refund, for example two or more identical duplicate \
charges (same amount, currency and description), always target the most recently created one, using each \
transaction's created timestamp. If that one has no refundable amount left, use the most recent one that \
does. This keeps the choice deterministic, so any two reviews of the same history pick the same charge.
- If the billing history doesn't support the customer's claim (no matching charge, or the charge is \
legitimate on inspection), the correct action is usually "no_action", with a rationale explaining why, not \
a refund.
- If there isn't enough billing history to be confident and this is NOT a fraud/theft signal (see the fraud \
rule above, which takes priority), propose "escalate" rather than guessing.
- If the customer explicitly asks to cancel their subscription, propose "cancel_subscription" with \
target_subscription_id set to a subscription id that actually appears in the provided subscriptions list, \
never invent one. If no matching subscription exists, propose "escalate" instead.
- For a goodwill or dissatisfaction request where a refund isn't clearly warranted by the billing evidence \
but some gesture seems reasonable, "apply_account_credit" is available as an alternative to "refund". Only \
propose it when there's a real Stripe customer to credit.
- The customer's recent ticket count is context, not a verdict either way. A high count doesn't \
automatically justify sympathy or suspicion, but it's worth factoring in if it's relevant to this specific \
claim (e.g. a pattern of repeated similar disputes).
- Your action_type must be consistent with your own rationale.

Output: action_type, amount (integer cents, 0 unless action_type is refund/partial_refund/apply_account_credit), \
currency, target_transaction_id (empty string unless action_type is refund/partial_refund), \
target_subscription_id (empty string unless action_type is cancel_subscription), rationale (2-4 sentences), \
and confidence (0-1)."""


def _format_prompt(ticket_text: str, stripe_history: StripeHistory, ticket_count_last_90_days: int) -> str:
    return (
        f"Ticket text:\n{ticket_text}\n\n"
        f"Customer's ticket count in the last 90 days (including this one): {ticket_count_last_90_days}\n\n"
        f"Stripe billing history for this customer (freshly re-pulled for this review):\n"
        f"{stripe_history.model_dump_json(indent=2)}"
    )


async def _derive_independent_conclusion(
    ticket_text: str, stripe_history: StripeHistory, ticket_count_last_90_days: int
) -> ProposedAction:
    """The only model call in this handler. Takes raw facts only, never the
    worker's proposed_action or its rationale."""
    result = await generate_json(
        prompt=_format_prompt(ticket_text, stripe_history, ticket_count_last_90_days),
        schema=_SCHEMA,
        model=VERIFIER_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    if not result.get("target_transaction_id"):
        result["target_transaction_id"] = None
    if not result.get("target_subscription_id"):
        result["target_subscription_id"] = None
    return ProposedAction.model_validate(result)


def _count_recent_tickets(customer_email: str, days: int = 90) -> int:
    """dynamo.list_tickets() has no direct customer_email plus date-range
    query path (its own module docstring explains why some filters run in
    Python rather than as a DynamoDB key condition), so this walks the
    full ticket list through that same public function and filters here,
    rather than reaching into the table or its GSIs directly. Fine at the
    scale list_tickets() itself is already documented to accept; a
    dedicated CustomerIndex would be the next step if that ever changes."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    result = dynamo.list_tickets(page=1, page_size=10_000)
    return sum(
        1
        for t in result["tickets"]
        if t.get("customer_email") == customer_email and t["created_at"] >= cutoff
    )


def _check_consistency(worker_action: ProposedAction, verifier_action: ProposedAction) -> VerificationResult:
    """Only compares structured fields, never the worker's rationale prose."""
    fields_match = (
        worker_action.action_type == verifier_action.action_type
        and worker_action.amount == verifier_action.amount
        and worker_action.target_transaction_id == verifier_action.target_transaction_id
        and worker_action.target_subscription_id == verifier_action.target_subscription_id
    )
    low_confidence = verifier_action.confidence < _LOW_CONFIDENCE_THRESHOLD
    consistent = fields_match and not low_confidence

    if not fields_match:
        mismatch_type = (
            f"{worker_action.action_type}_vs_{verifier_action.action_type}"
            if worker_action.action_type != verifier_action.action_type
            else "amount_or_target_mismatch"
        )
        notes = (
            f"Worker proposed {worker_action.action_type} (amount={worker_action.amount}, "
            f"target_transaction={worker_action.target_transaction_id!r}, "
            f"target_subscription={worker_action.target_subscription_id!r}). Independent verification reached a "
            f"different conclusion: {verifier_action.action_type} (amount={verifier_action.amount}, "
            f"target_transaction={verifier_action.target_transaction_id!r}, "
            f"target_subscription={verifier_action.target_subscription_id!r})."
        )
    elif low_confidence:
        mismatch_type = "low_confidence"
        notes = (
            f"Worker and verifier agree on {worker_action.action_type}, but the verifier's independent "
            f"confidence ({verifier_action.confidence:.2f}) is below the {_LOW_CONFIDENCE_THRESHOLD} "
            f"threshold for auto-execution. Routing to human review rather than executing on a shaky signal."
        )
    else:
        mismatch_type = None
        notes = f"Worker and verifier independently agree: {worker_action.action_type}."

    return VerificationResult(
        consistent=consistent,
        mismatch_type=mismatch_type,
        verifier_rationale=verifier_action.rationale,
        notes=notes,
        final_decision="execute" if consistent else "escalate",
    )


async def _verify_ticket(ticket_id: str) -> dict:
    ticket = dynamo.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} not found")

    dynamo.update_ticket_status(ticket_id, "verifier_review")

    # Everything up to and including the model call below uses only raw
    # facts refetched here, never anything the worker concluded.
    ticket_text = ticket["ticket_text"]
    customer_email = ticket["customer_email"]
    stripe_history = await investigate_customer(customer_email)
    ticket_count_last_90_days = _count_recent_tickets(customer_email)
    verifier_action = await _derive_independent_conclusion(ticket_text, stripe_history, ticket_count_last_90_days)

    dynamo.append_audit_record(
        ticket_id, "cross_referenced", "verifier_agent", verifier_action.model_dump(mode="json")
    )

    # Only now, after the independent conclusion above already exists, do
    # we read what the worker proposed.
    worker_action = ProposedAction.model_validate(ticket["proposed_action"])
    verification = _check_consistency(worker_action, verifier_action)

    dynamo.write_verification_result(
        ticket_id=ticket_id,
        consistent=verification.consistent,
        mismatch_type=verification.mismatch_type,
        verifier_rationale=verification.verifier_rationale,
        notes=verification.notes,
        final_decision=verification.final_decision,
        raw_verification_data=stripe_history.model_dump(mode="json"),
    )
    dynamo.append_audit_record(
        ticket_id, "verification_result", "verifier_agent", verification.model_dump(mode="json")
    )

    return {
        "ticket_id": ticket_id,
        "consistent": verification.consistent,
        "mismatch_type": verification.mismatch_type,
        "final_decision": verification.final_decision,
    }


def lambda_handler(event: dict, context) -> dict:
    """Event contract: {"ticket_id": "<uuid>"}, expects the ticket to
    already have a proposed_action set by the worker pipeline and to have
    already cleared the OPA policy gate (that gate is a separate Step
    Functions state, not part of this handler).

    Response: {"ticket_id", "consistent", "mismatch_type", "final_decision"},
    final_decision is "execute" or "escalate", a Step Functions Choice
    state branches on it directly."""
    ticket_id = event["ticket_id"]
    logger.info("Verifying ticket %s", ticket_id)
    return asyncio.run(_verify_ticket(ticket_id))
