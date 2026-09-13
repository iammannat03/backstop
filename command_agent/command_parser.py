"""Command Parser: turns a human's free-text `@backstop <command>` reply into
the same structured ProposedAction shape the worker agent produces, so it can
flow through the identical OPA gate and executor.
"""

from shared.action_types import ACTION_TYPE_VALUES
from shared.models import ProposedAction, StripeHistory
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
    },
    "required": [
        "action_type",
        "amount",
        "currency",
        "target_transaction_id",
        "target_subscription_id",
        "rationale",
    ],
}

_SYSTEM_INSTRUCTION = """You are transcribing an explicit human instruction into a structured action for a \
billing support system. A support agent has typed a command (after "@backstop") in a Slack thread attached to \
a specific customer ticket, having already reviewed that ticket's full audit trail. Your job is to translate \
their instruction into the structured schema below, grounded against the customer's real Stripe billing \
history, not to second-guess whether the underlying ticket complaint was valid, and not to invent your own \
judgment about what should happen. The human has already made that call.

- Only set target_transaction_id to a transaction id that actually appears in the provided transactions list. \
Never invent one. If the command references "the last charge" or similar, resolve it against the most recent \
transaction in the list. If the command doesn't clearly map to any transaction in the list, propose "escalate" \
with a rationale explaining the ambiguity, do not guess.
- Never propose a refund amount larger than that transaction's own remaining refundable amount \
(amount - amount_refunded). If the command doesn't specify an amount for a refund, use the full remaining \
refundable amount.
- Only set target_subscription_id to a subscription id that actually appears in the provided subscriptions \
list, same rule as above, never invent one.
- If the command doesn't map to any of the known action types, or is too vague to act on safely (e.g. "look \
into this" with no concrete action), propose "escalate" with a rationale explaining why.
- amount is integer cents, 0 unless action_type is refund/partial_refund/apply_account_credit. currency \
defaults to the transaction's own currency, or "usd" if there's no target transaction.
- rationale should state what the human asked for and how you mapped it to the structured action (1-3 \
sentences), this is shown back to the human as confirmation of what's about to execute.

Output: action_type, amount, currency, target_transaction_id (empty string if not applicable), \
target_subscription_id (empty string if not applicable), rationale."""


def _format_prompt(command_text: str, ticket_text: str, stripe_history: StripeHistory) -> str:
    return (
        f"Human command (after @backstop):\n{command_text}\n\n"
        f"Original ticket text (for context only):\n{ticket_text}\n\n"
        f"Customer's real Stripe billing history:\n{stripe_history.model_dump_json(indent=2)}"
    )


async def parse_command(command_text: str, ticket_text: str, stripe_history: StripeHistory) -> ProposedAction:
    result = await generate_json(
        prompt=_format_prompt(command_text, ticket_text, stripe_history),
        schema=_SCHEMA,
        model=REASONING_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    if not result.get("target_transaction_id"):
        result["target_transaction_id"] = None
    if not result.get("target_subscription_id"):
        result["target_subscription_id"] = None
    # An explicit human instruction isn't a probabilistic inference, so
    # confidence is fixed at 1.0 rather than asked of the model.
    result["confidence"] = 1.0
    return ProposedAction.model_validate(result)
