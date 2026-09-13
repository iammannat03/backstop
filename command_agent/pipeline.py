"""Command pipeline: the second, human-triggered way an action reaches
execution, alongside worker_agent.pipeline's auto-detected path. Runs when a
human replies "@backstop <command>" in the thread under a ticket's Slack post.

A deliberately shorter chain than the auto pipeline: OPA still applies in
full, but the verifier's independent re-derivation is skipped, there is
nothing to re-derive against once a human has explicitly stated the action.

A "deny" still blocks even a human command. An "escalate" flag does not
block here, since a human is already the approving party those flags route to.
"""

import logging
import uuid

from audit.slack_notifier import notify_command_blocked, notify_command_needs_clarification
from execution.stripe_executor import execute_action
from governance.opa_client import build_opa_input, evaluate_policy
from persistence.db import SessionLocal
from persistence.models import AuditRecord, PolicyDecision, Ticket
from shared.models import ProposedAction
from verifier_agent.cross_reference import count_recent_tickets
from worker_agent.action_proposer import validate_against_stripe
from worker_agent.stripe_investigator import investigate_customer

from command_agent.command_parser import parse_command

logger = logging.getLogger("command_agent.pipeline")

# Same shape as verifier_agent.pipeline's NEVER_AUTO_EXECUTE: these mean "not
# enough grounding to act," so the command needs the human to clarify.
_NEEDS_CLARIFICATION = {"escalate", "flag_for_fraud_review"}


def _load_ticket(ticket_id: uuid.UUID) -> tuple[str, str, str | None]:
    """Returns (ticket_text, customer_email, zendesk_ticket_id)."""
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        return ticket.ticket_text, ticket.customer_email, ticket.zendesk_ticket_id


def _set_status(ticket_id: uuid.UUID, status: str) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        ticket.status = status
        db.commit()


def _persist_action(ticket_id: uuid.UUID, action: ProposedAction) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        ticket.proposed_action = action.model_dump(mode="json")
        db.commit()


def _log_event(ticket_id: uuid.UUID, event_type: str, actor: str, detail: dict) -> None:
    with SessionLocal() as db:
        db.add(AuditRecord(ticket_id=ticket_id, event_type=event_type, actor=actor, detail=detail))
        db.commit()


async def run_command_pipeline(ticket_id: uuid.UUID, command_text: str, slack_user_id: str) -> None:
    actor = f"human:{slack_user_id}"
    ticket_text, customer_email, _zendesk_ticket_id = _load_ticket(ticket_id)

    _log_event(ticket_id, "command_received", actor, {"command_text": command_text})

    stripe_history = await investigate_customer(customer_email)
    raw_action = await parse_command(command_text, ticket_text, stripe_history)
    _log_event(ticket_id, "command_parsed", actor, raw_action.model_dump(mode="json"))

    action = validate_against_stripe(raw_action, stripe_history)
    if action is not raw_action:
        _log_event(ticket_id, "command_downgraded", actor, action.model_dump(mode="json"))

    ticket_count_last_90_days = count_recent_tickets(customer_email)
    has_fraud_flag = str(stripe_history.customer_metadata.get("fraud_flag", "")).lower() in ("true", "1")
    target_transaction = next(
        (t for t in stripe_history.transactions if t.id == action.target_transaction_id), None
    )
    target_subscription = next(
        (s for s in stripe_history.subscriptions if s.id == action.target_subscription_id), None
    )

    opa_input = build_opa_input(
        action,
        stripe_history.customer_id,
        stripe_history.refunds_last_30_days,
        ticket_count_last_90_days,
        has_fraud_flag,
        target_transaction,
        target_subscription,
    )
    policy_result = await evaluate_policy(opa_input)

    with SessionLocal() as db:
        db.add(
            PolicyDecision(
                ticket_id=ticket_id,
                decision=policy_result["decision"],
                matched_rule=policy_result["matched_rule"],
                reason=policy_result["reason"],
                raw_input=opa_input,
                raw_output=policy_result,
            )
        )
        db.commit()
    _log_event(ticket_id, "command_policy_decision", actor, policy_result)

    if policy_result["decision"] == "deny":
        logger.info(
            "Ticket %s: command %r denied by policy: %s", ticket_id, command_text, policy_result["matched_rule"]
        )
        await notify_command_blocked(
            ticket_id, command_text, policy_result["matched_rule"], policy_result["reason"]
        )
        return

    if action.action_type in _NEEDS_CLARIFICATION:
        logger.info(
            "Ticket %s: command %r could not be grounded in a concrete action, asking for clarification",
            ticket_id,
            command_text,
        )
        await notify_command_needs_clarification(ticket_id, command_text, action.rationale)
        return

    logger.info("Ticket %s: executing human command %r (%s)", ticket_id, command_text, action.action_type)
    _persist_action(ticket_id, action)
    _set_status(ticket_id, "executing")
    await execute_action(ticket_id, action)
