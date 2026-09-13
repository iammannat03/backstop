"""Worker agent orchestrator: runs classify -> investigate -> reason -> propose
for one ticket. Every await here yields control back to the event loop so
other tickets' pipelines keep moving concurrently.
"""

import logging
import uuid

from persistence.db import SessionLocal
from persistence.models import AuditRecord, Ticket
from shared.models import Classification, ProposedAction, StripeHistory
from worker_agent.action_proposer import propose_action
from worker_agent.classifier import classify_ticket
from worker_agent.reasoning_engine import reason_about_ticket
from worker_agent.stripe_investigator import investigate_customer

logger = logging.getLogger("worker_agent.pipeline")


def _load_ticket(ticket_id: uuid.UUID) -> tuple[str, str]:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        return ticket.ticket_text, ticket.customer_email


def _set_status(ticket_id: uuid.UUID, status: str) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        ticket.status = status
        db.commit()


def _set_classification(ticket_id: uuid.UUID, classification: Classification) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        ticket.classification = classification.model_dump(mode="json")
        db.commit()


def _log_event(ticket_id: uuid.UUID, event_type: str, actor: str, detail: dict) -> None:
    with SessionLocal() as db:
        db.add(AuditRecord(ticket_id=ticket_id, event_type=event_type, actor=actor, detail=detail))
        db.commit()


async def run_worker_pipeline(ticket_id: uuid.UUID) -> ProposedAction:
    ticket_text, customer_email = _load_ticket(ticket_id)
    _set_status(ticket_id, "investigating")

    classification = await classify_ticket(ticket_text)
    _log_event(ticket_id, "classified", "worker_agent", classification.model_dump(mode="json"))
    _set_classification(ticket_id, classification)

    if not classification.is_billing_relevant:
        # Skip the expensive Stripe/reasoning stage entirely for non-billing tickets.
        logger.info("Ticket %s classified as not billing-relevant, skipping investigation/reasoning", ticket_id)
        stripe_history = StripeHistory(customer_id=None)
        raw_action = ProposedAction(
            action_type="no_action",
            amount=0,
            currency="usd",
            target_transaction_id=None,
            rationale="Classified as not billing-relevant, no Stripe investigation or reasoning performed.",
            confidence=classification.confidence,
        )
    else:
        stripe_history = await investigate_customer(customer_email)
        _log_event(ticket_id, "stripe_investigated", "worker_agent", stripe_history.model_dump(mode="json"))

        raw_action = await reason_about_ticket(ticket_text, classification, stripe_history)
        # Logged separately from the "proposed_action" event so the audit trail
        # still shows the original model output even if it gets downgraded.
        _log_event(ticket_id, "reasoned", "worker_agent", raw_action.model_dump(mode="json"))

    proposed = await propose_action(ticket_id, raw_action, stripe_history)
    return proposed
