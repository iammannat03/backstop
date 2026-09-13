"""Reasoning Engine: the actual judgment call. Reconciles what the customer
implied against what actually happened in Stripe, including recognizing a
legitimate proration even if the customer assumes it's an error.
"""

from shared.action_types import ACTION_TYPE_VALUES
from shared.models import Classification, ProposedAction, StripeHistory
from worker_agent.gemini_client import REASONING_MODEL, generate_json

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
        "customer_message": {"type": "STRING"},
    },
    "required": [
        "action_type",
        "amount",
        "currency",
        "target_transaction_id",
        "target_subscription_id",
        "rationale",
        "confidence",
        "customer_message",
    ],
}

_SYSTEM_INSTRUCTION = """You are the reasoning engine for a billing support agent. You are given the raw \
customer ticket text, a fast (non-authoritative) classification of it, and the customer's real Stripe \
billing history: transactions (charges), subscriptions, and account metadata.

Reconcile what the customer is asking for against what actually happened in Stripe. Do not trust the \
customer's framing at face value.

- Check first, before any other rule below: if the ticket describes an unauthorized charge, a \
stolen/compromised card, or a suspected account takeover, propose "flag_for_fraud_review", never "escalate", \
never a refund or credit, regardless of whether Stripe billing history exists or not. This is a categorical \
safety signal, not a data-sufficiency question. "I don't have enough billing history" is not a reason to \
fall back to plain "escalate" when the ticket is actually describing a fraud/theft signal. Detecting fraud \
is what determines this action_type, not the amount of billing evidence available.
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
- If there isn't enough billing history to be confident (e.g. no Stripe customer or charges found at all) \
and this is NOT a fraud/theft signal (see the fraud rule above, which takes priority), propose "escalate" \
rather than guessing.
- If the customer explicitly asks to cancel their subscription, propose "cancel_subscription" with \
target_subscription_id set to a subscription id that actually appears in the provided subscriptions list, \
never invent one, same rule as for refund transaction ids. If no matching subscription exists, propose \
"escalate" instead.
- For a goodwill or dissatisfaction request where a refund isn't clearly warranted by the billing evidence \
but some gesture seems reasonable, "apply_account_credit" is available as an alternative to "refund". Only \
propose it when there's a real Stripe customer to credit.
- Your action_type must be consistent with your own rationale: if your rationale concludes a charge was \
legitimate, do not propose refunding it.

- customer_message: only when action_type is "no_action" AND real billing history was actually investigated \
(not when there was no matching Stripe customer at all), write a plain, specific, customer-facing explanation \
of why no billing action is being taken, grounded in the real Stripe data (e.g. "this was a legitimate \
proration from your plan change" or "these are two separate charges: your subscription renewal and an \
add-on purchase"). Never include an internal identifier (a Stripe id like ch_..., sub_..., or cus_...); refer \
to charges by date and amount. Never add a generic conversational closer like "let us know if you have \
questions". State the outcome plainly and stop. For every other action_type, or when there's no real billing \
history to explain, leave this empty, the system already has a specific default message for those.

Output: action_type, amount (integer cents, 0 unless action_type is refund/partial_refund/apply_account_credit), \
currency, target_transaction_id (empty string unless action_type is refund/partial_refund), \
target_subscription_id (empty string unless action_type is cancel_subscription), rationale (2-4 sentences, \
this will be shown to a human reviewer, so make the reasoning legible, not just a verdict), confidence (0-1), \
customer_message (empty string if not applicable)."""


def _format_prompt(ticket_text: str, classification: Classification, stripe_history: StripeHistory) -> str:
    return (
        f"Ticket text:\n{ticket_text}\n\n"
        f"Classification (informational, not authoritative):\n{classification.model_dump_json()}\n\n"
        f"Stripe billing history for this customer:\n{stripe_history.model_dump_json(indent=2)}"
    )


async def reason_about_ticket(
    ticket_text: str, classification: Classification, stripe_history: StripeHistory
) -> ProposedAction:
    result = await generate_json(
        prompt=_format_prompt(ticket_text, classification, stripe_history),
        schema=_SCHEMA,
        model=REASONING_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    if not result.get("target_transaction_id"):
        result["target_transaction_id"] = None
    if not result.get("target_subscription_id"):
        result["target_subscription_id"] = None
    if not result.get("customer_message"):
        result["customer_message"] = None
    return ProposedAction.model_validate(result)
