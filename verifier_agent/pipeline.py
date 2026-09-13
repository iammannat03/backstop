"""Verification orchestrator: the second half of the per-ticket chain.
Wires together the OPA policy gate and the verifier agent (cross_reference +
consistency_checker): worker action -> OPA -> [deny/escalate -> human] or
[allow -> verifier] -> [inconsistent -> human] or [consistent -> executing].
"""

import logging
import uuid

from execution.stripe_executor import execute_action
from governance.opa_client import build_opa_input, evaluate_policy
from persistence.db import SessionLocal
from persistence.models import AuditRecord, PolicyDecision, Ticket
from persistence.models import VerificationResult as VerificationResultRow
from shared.models import ProposedAction
from verifier_agent.consistency_checker import check_consistency
from verifier_agent.cross_reference import count_recent_tickets, cross_reference
from worker_agent.stripe_investigator import investigate_customer

logger = logging.getLogger("verifier_agent.pipeline")

# Agreeing on one of these means "we agree a human must handle this," never
# "safe to auto-execute."
NEVER_AUTO_EXECUTE = {"escalate", "flag_for_fraud_review"}


def _load_ticket_context(ticket_id: uuid.UUID) -> tuple[str, str]:
    """Returns (customer_email, zendesk_ticket_id)."""
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} not found")
        return ticket.customer_email, ticket.zendesk_ticket_id


def _set_status(ticket_id: uuid.UUID, status: str) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        ticket.status = status
        db.commit()


def _log_event(ticket_id: uuid.UUID, event_type: str, actor: str, detail: dict) -> None:
    with SessionLocal() as db:
        db.add(AuditRecord(ticket_id=ticket_id, event_type=event_type, actor=actor, detail=detail))
        db.commit()


async def run_verification_pipeline(ticket_id: uuid.UUID, worker_action: ProposedAction) -> None:
    customer_email, zendesk_ticket_id = _load_ticket_context(ticket_id)
    _set_status(ticket_id, "opa_review")

    stripe_history = await investigate_customer(customer_email)
    ticket_count_last_90_days = count_recent_tickets(customer_email)
    has_fraud_flag = str(stripe_history.customer_metadata.get("fraud_flag", "")).lower() in ("true", "1")
    target_transaction = next(
        (t for t in stripe_history.transactions if t.id == worker_action.target_transaction_id), None
    )
    target_subscription = next(
        (s for s in stripe_history.subscriptions if s.id == worker_action.target_subscription_id), None
    )

    opa_input = build_opa_input(
        worker_action,
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
    _log_event(ticket_id, "policy_decision", "opa", policy_result)

    if policy_result["decision"] != "allow":
        logger.info(
            "Ticket %s %s by policy: %s", ticket_id, policy_result["decision"], policy_result["matched_rule"]
        )
        _log_event(
            ticket_id,
            "blocked",
            "opa",
            {"decision": policy_result["decision"], "matched_rule": policy_result["matched_rule"], "reason": policy_result["reason"]},
        )
        _set_status(ticket_id, "escalated")
        return

    _set_status(ticket_id, "verifier_review")
    cr = await cross_reference(ticket_id, customer_email)
    _log_event(ticket_id, "cross_referenced", "verifier_agent", cr.verifier_action.model_dump(mode="json"))

    verification = check_consistency(worker_action, cr.verifier_action)

    with SessionLocal() as db:
        db.add(
            VerificationResultRow(
                ticket_id=ticket_id,
                consistent=verification.consistent,
                mismatch_type=verification.mismatch_type,
                verifier_rationale=verification.verifier_rationale,
                notes=verification.notes,
                final_decision=verification.final_decision,
                raw_verification_data=cr.stripe_history.model_dump(mode="json"),
            )
        )
        db.commit()
    _log_event(ticket_id, "verification_result", "verifier_agent", verification.model_dump(mode="json"))

    if verification.consistent and worker_action.action_type in NEVER_AUTO_EXECUTE:
        logger.info(
            "Ticket %s: worker and verifier agree on %s, routing to human", ticket_id, worker_action.action_type
        )
        _set_status(ticket_id, "escalated")
    elif verification.consistent:
        logger.info("Ticket %s verified consistent, executing", ticket_id)
        _set_status(ticket_id, "executing")
        await execute_action(ticket_id, worker_action)
    else:
        logger.info("Ticket %s verification mismatch (%s), escalating", ticket_id, verification.mismatch_type)
        _set_status(ticket_id, "escalated")
