"""Lambda entry point for the classify step of the worker pipeline.

Input event: {"ticket_id": str}
Output: {"ticket_id": str, "is_billing_relevant": bool, "classification": dict}

The state machine branches on is_billing_relevant: false skips straight to
propose_handler with a synthesized no_action payload (see propose_handler.py),
true continues on to investigate_handler.
"""

import asyncio

from persistence import dynamo
from worker_agent.classifier import classify_ticket


def lambda_handler(event: dict, context) -> dict:
    ticket_id = event["ticket_id"]
    ticket = dynamo.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} not found")

    dynamo.update_ticket_status(ticket_id, "investigating")

    classification = asyncio.run(classify_ticket(ticket["ticket_text"]))
    classification_dict = classification.model_dump(mode="json")

    dynamo.set_ticket_classification(ticket_id, classification_dict)
    dynamo.append_audit_record(ticket_id, "classified", "worker_agent", classification_dict)

    return {
        "ticket_id": ticket_id,
        "is_billing_relevant": classification.is_billing_relevant,
        "classification": classification_dict,
    }
