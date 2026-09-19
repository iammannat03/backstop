"""Lambda entry point for the propose step of the worker pipeline. Terminal
step of the worker side, runs the hallucination guard and persists the
ticket's proposed_action.

Input event: {"ticket_id": str, "raw_action": dict, "stripe_history": dict}
Output: {"ticket_id": str, "proposed_action": dict}

Normal path: called with reason_handler's output as-is.

Short-circuit path (classify_handler returned is_billing_relevant=false):
the state machine skips investigate_handler and reason_handler entirely and
calls this directly with a synthesized no_action raw_action and an empty
stripe_history, exactly mirroring backstop-prac's pipeline.py else branch.
validate_against_stripe is a no-op for no_action, so this handler needs no
special case for it:

    {
        "ticket_id": ticket_id,
        "raw_action": {
            "action_type": "no_action",
            "amount": 0,
            "currency": "usd",
            "target_transaction_id": None,
            "target_subscription_id": None,
            "rationale": "Classified as not billing-relevant, no Stripe investigation or reasoning performed.",
            "confidence": <classification.confidence>,
            "customer_message": None,
        },
        "stripe_history": {"customer_id": None},
    }
"""

from shared.models import ProposedAction, StripeHistory
from worker_agent.action_proposer import propose_action


def lambda_handler(event: dict, context) -> dict:
    ticket_id = event["ticket_id"]
    raw_action = ProposedAction.model_validate(event["raw_action"])
    stripe_history = StripeHistory.model_validate(event["stripe_history"])

    validated = propose_action(ticket_id, raw_action, stripe_history)

    return {"ticket_id": ticket_id, "proposed_action": validated.model_dump(mode="json")}
