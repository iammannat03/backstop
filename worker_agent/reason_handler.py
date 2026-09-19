"""Lambda entry point for the reason step of the worker pipeline.

Input event: {"ticket_id": str, "stripe_history": dict}
Output: {"ticket_id": str, "raw_action": dict, "stripe_history": dict}

raw_action is the reasoning engine's unvalidated output, propose_handler
still has to run it through the hallucination guard before anything is
persisted as the ticket's proposed_action.
"""

import asyncio

from persistence import dynamo
from shared.models import Classification, StripeHistory
from worker_agent.reasoning_engine import reason_about_ticket


def lambda_handler(event: dict, context) -> dict:
    ticket_id = event["ticket_id"]
    ticket = dynamo.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} not found")

    classification = Classification.model_validate(ticket["classification"])
    stripe_history = StripeHistory.model_validate(event["stripe_history"])

    raw_action = asyncio.run(reason_about_ticket(ticket["ticket_text"], classification, stripe_history))
    raw_action_dict = raw_action.model_dump(mode="json")

    dynamo.append_audit_record(ticket_id, "reasoned", "worker_agent", raw_action_dict)

    return {"ticket_id": ticket_id, "raw_action": raw_action_dict, "stripe_history": event["stripe_history"]}
