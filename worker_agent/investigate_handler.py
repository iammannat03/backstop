"""Lambda entry point for the investigate step of the worker pipeline.

Input event: {"ticket_id": str}
Output: {"ticket_id": str, "stripe_history": dict}

stripe_history is handed forward through the state machine to reason_handler
rather than re-fetched, matching backstop-prac's pipeline.py which passed the
same in-memory StripeHistory from investigate straight into reasoning.
"""

import asyncio

from persistence import dynamo
from worker_agent.stripe_investigator import investigate_customer


def lambda_handler(event: dict, context) -> dict:
    ticket_id = event["ticket_id"]
    ticket = dynamo.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} not found")

    stripe_history = asyncio.run(investigate_customer(ticket["customer_email"]))
    stripe_history_dict = stripe_history.model_dump(mode="json")

    dynamo.append_audit_record(ticket_id, "stripe_investigated", "worker_agent", stripe_history_dict)

    return {"ticket_id": ticket_id, "stripe_history": stripe_history_dict}
