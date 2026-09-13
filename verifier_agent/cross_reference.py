"""Cross-Reference Engine: independently re-derives an answer for a ticket.
Re-reads the raw ticket text and re-pulls Stripe history fresh; never reads
the worker's proposed action or rationale.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import func, select

from persistence.db import SessionLocal
from persistence.models import Ticket
from shared.action_types import ACTION_TYPE_VALUES
from shared.models import ProposedAction, StripeHistory
from worker_agent.gemini_client import REASONING_MODEL, generate_json
from worker_agent.stripe_investigator import investigate_customer

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "action_type": {"type": "STRING", "enum": ACTION_TYPE_VALUES},
        "amount": {"type": "INTEGER"},
        "currency": {"type": "STRING"},
        "target_transaction_id": {"type": "STRING"},
        "target_subscription_id": {"type": "STRING"},
        "rationale": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
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


class CrossReferenceResult(NamedTuple):
    ticket_text: str
    stripe_history: StripeHistory
    ticket_count_last_90_days: int
    verifier_action: ProposedAction


def _refetch_ticket_text(ticket_id: uuid.UUID) -> str:
    """A fresh Postgres read, not whatever the worker's pipeline had in memory."""
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        return ticket.ticket_text


def count_recent_tickets(customer_email: str, days: int = 90) -> int:
    """The customer's past ticket pattern, also used by the OPA input assembly."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        count = db.execute(
            select(func.count()).select_from(Ticket).where(
                Ticket.customer_email == customer_email,
                Ticket.created_at >= cutoff,
            )
        ).scalar_one()
    return count


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
    result = await generate_json(
        prompt=_format_prompt(ticket_text, stripe_history, ticket_count_last_90_days),
        schema=_SCHEMA,
        model=REASONING_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    if not result.get("target_transaction_id"):
        result["target_transaction_id"] = None
    if not result.get("target_subscription_id"):
        result["target_subscription_id"] = None
    return ProposedAction.model_validate(result)


async def cross_reference(ticket_id: uuid.UUID, customer_email: str) -> CrossReferenceResult:
    ticket_text = _refetch_ticket_text(ticket_id)
    stripe_history = await investigate_customer(customer_email)
    ticket_count_last_90_days = count_recent_tickets(customer_email)
    verifier_action = await _derive_independent_conclusion(ticket_text, stripe_history, ticket_count_last_90_days)
    return CrossReferenceResult(ticket_text, stripe_history, ticket_count_last_90_days, verifier_action)
