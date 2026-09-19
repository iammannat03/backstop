"""Ticket Classifier: fast first pass that filters and routes billing-relevant
tickets, without investigating Stripe or reasoning about what happened.
"""

from shared.models import Classification
from worker_agent.bedrock_client import CLASSIFIER_MODEL, generate_json

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_billing_relevant": {"type": "boolean"},
        "intent": {
            "type": "string",
            "enum": [
                "refund_request",
                "duplicate_charge",
                "billing_inquiry",
                "cancellation_request",
                "subscription_change",
                "other",
            ],
        },
        "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence": {"type": "number"},
    },
    "required": ["is_billing_relevant", "intent", "urgency", "confidence"],
}

_SYSTEM_INSTRUCTION = (
    "You are the first-pass classifier for a billing support pipeline. Read the raw "
    "customer ticket text and classify it fast. You are not deciding what action to "
    "take, only what kind of ticket this is and how urgent it looks. is_billing_relevant "
    "should be false for anything not about billing, charges, subscriptions, or refunds "
    "(e.g. a product bug report or a feature request)."
)


async def classify_ticket(ticket_text: str) -> Classification:
    result = await generate_json(
        prompt=f"Ticket text:\n{ticket_text}",
        schema=_SCHEMA,
        model=CLASSIFIER_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    return Classification.model_validate(result)
