"""Stripe Action Executor: the only module with permission to actually move
money. Fires only after both OPA and the verifier have cleared the action,
and trusts that gate completely, it does not re-derive or re-check anything.

Also closes the loop at each terminal outcome: posts a Slack notification and
writes the resolution back to the original Zendesk ticket.
"""

import logging
import os
import uuid

import httpx
from dotenv import load_dotenv

from audit.slack_notifier import notify_executed, notify_execution_failed
from audit.zendesk_updater import mark_escalated, mark_resolved
from persistence.db import SessionLocal
from persistence.models import AuditRecord, Ticket
from shared.models import ProposedAction

load_dotenv()

logger = logging.getLogger("execution.stripe_executor")

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_BASE_URL = "https://api.stripe.com/v1"


def _auth() -> tuple[str, str]:
    return (STRIPE_API_KEY, "")


async def _cancel_subscription(ticket_id: uuid.UUID, subscription_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(
            f"{STRIPE_BASE_URL}/subscriptions/{subscription_id}",
            headers={"Idempotency-Key": f"backstop-cancel-{ticket_id}"},
            auth=_auth(),
        )
        resp.raise_for_status()
        return resp.json()


async def _apply_account_credit(ticket_id: uuid.UUID, customer_id: str, amount: int, currency: str) -> dict:
    # A negative balance_transaction amount credits future invoices rather
    # than moving money directly.
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{STRIPE_BASE_URL}/customers/{customer_id}/balance_transactions",
            data={"amount": -amount, "currency": currency},
            headers={"Idempotency-Key": f"backstop-credit-{ticket_id}"},
            auth=_auth(),
        )
        resp.raise_for_status()
        return resp.json()


def _load_stripe_customer_id(ticket_id: uuid.UUID) -> str | None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        return ticket.customer_id if ticket else None


async def _issue_refund(ticket_id: uuid.UUID, charge_id: str, amount: int) -> dict:
    # Idempotency-Key tied to the ticket id so a retried execution can never
    # double-refund.
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{STRIPE_BASE_URL}/refunds",
            data={"charge": charge_id, "amount": amount},
            headers={"Idempotency-Key": f"backstop-refund-{ticket_id}"},
            auth=_auth(),
        )
        resp.raise_for_status()
        return resp.json()


def _set_status(ticket_id: uuid.UUID, status: str) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        ticket.status = status
        db.commit()


def _log_event(ticket_id: uuid.UUID, event_type: str, actor: str, detail: dict) -> None:
    with SessionLocal() as db:
        db.add(AuditRecord(ticket_id=ticket_id, event_type=event_type, actor=actor, detail=detail))
        db.commit()


def _load_zendesk_ticket_id(ticket_id: uuid.UUID) -> str | None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        return ticket.zendesk_ticket_id if ticket else None


def _customer_facing_resolution_message(action: ProposedAction) -> str:
    if action.action_type in ("refund", "partial_refund"):
        return f"We've reviewed your account and issued a refund of ${action.amount / 100:.2f} for this charge."
    if action.action_type == "cancel_subscription":
        return "Your subscription has been cancelled as requested."
    if action.action_type == "apply_account_credit":
        return f"We've applied a ${action.amount / 100:.2f} credit to your account balance."
    # "no_action" covers both a charge that turned out to be correct and a
    # ticket that was never about billing at all, so this stays generic.
    return "Thanks for reaching out. After review, no billing action was needed on this ticket."


async def _close_out(ticket_id: uuid.UUID, action: ProposedAction, refund_id: str | None) -> None:
    """Slack + Zendesk write-back for the success path."""
    zendesk_ticket_id = _load_zendesk_ticket_id(ticket_id)
    await notify_executed(
        ticket_id, action.action_type, action.amount, action.currency, action.rationale, refund_id
    )
    if zendesk_ticket_id:
        await mark_resolved(zendesk_ticket_id, _customer_facing_resolution_message(action))


async def _fail_out(ticket_id: uuid.UUID, error_detail: str) -> None:
    """Slack + Zendesk write-back for every execution-failure branch."""
    zendesk_ticket_id = _load_zendesk_ticket_id(ticket_id)
    await notify_execution_failed(ticket_id, error_detail)
    if zendesk_ticket_id:
        await mark_escalated(
            zendesk_ticket_id, f"Automated execution failed and needs manual handling: {error_detail}"
        )


# Requires no Stripe call at all, resolving directly is the correct outcome.
_NO_STRIPE_CALL_NEEDED = {"no_action"}


async def execute_action(ticket_id: uuid.UUID, action: ProposedAction) -> None:
    """Called only once the verifier has marked the ticket "executing"."""
    if action.action_type in _NO_STRIPE_CALL_NEEDED:
        logger.info(
            "Ticket %s: action_type=%s requires no Stripe call, resolving directly", ticket_id, action.action_type
        )
        _log_event(ticket_id, "executed", "execution", {"action_type": action.action_type, "stripe_result": None})
        _set_status(ticket_id, "resolved")
        await _close_out(ticket_id, action, refund_id=None)
        return

    if action.action_type not in ("refund", "partial_refund", "cancel_subscription", "apply_account_credit"):
        # No branch for this action_type. Escalate instead of silently
        # marking it resolved with no real-world effect.
        logger.error(
            "Ticket %s: no execution handler for action_type=%s, escalating instead of silently resolving",
            ticket_id,
            action.action_type,
        )
        _log_event(
            ticket_id, "execution_failed", "execution", {"reason": "no handler for action_type", "action_type": action.action_type}
        )
        _set_status(ticket_id, "escalated")
        await _fail_out(ticket_id, f"No execution handler exists yet for action_type={action.action_type!r}.")
        return

    if action.action_type == "cancel_subscription":
        if not action.target_subscription_id:
            logger.error("Ticket %s: cancel_subscription reached execution with no target_subscription_id", ticket_id)
            _log_event(ticket_id, "execution_failed", "execution", {"reason": "missing target_subscription_id"})
            _set_status(ticket_id, "escalated")
            await _fail_out(ticket_id, "Proposed action had no valid target subscription to cancel.")
            return
        try:
            result = await _cancel_subscription(ticket_id, action.target_subscription_id)
            logger.info("Ticket %s: subscription %s cancelled", ticket_id, action.target_subscription_id)
            _log_event(
                ticket_id,
                "executed",
                "execution",
                {"target_subscription_id": action.target_subscription_id, "stripe_result": result},
            )
            _set_status(ticket_id, "resolved")
            await _close_out(ticket_id, action, refund_id=None)
        except httpx.HTTPStatusError as e:
            logger.exception("Ticket %s: Stripe subscription cancellation failed", ticket_id)
            _log_event(
                ticket_id, "execution_failed", "execution", {"status_code": e.response.status_code, "response": e.response.text}
            )
            _set_status(ticket_id, "escalated")
            await _fail_out(ticket_id, f"Stripe error {e.response.status_code}: {e.response.text}")
        return

    if action.action_type == "apply_account_credit":
        customer_id = _load_stripe_customer_id(ticket_id)
        if not customer_id:
            logger.error("Ticket %s: apply_account_credit reached execution with no known Stripe customer", ticket_id)
            _log_event(ticket_id, "execution_failed", "execution", {"reason": "missing customer_id"})
            _set_status(ticket_id, "escalated")
            await _fail_out(ticket_id, "Proposed action had no known Stripe customer to credit.")
            return
        try:
            result = await _apply_account_credit(ticket_id, customer_id, action.amount, action.currency)
            logger.info("Ticket %s: credited %d %s to customer %s", ticket_id, action.amount, action.currency, customer_id)
            _log_event(
                ticket_id,
                "executed",
                "execution",
                {"customer_id": customer_id, "amount": action.amount, "currency": action.currency, "stripe_result": result},
            )
            _set_status(ticket_id, "resolved")
            await _close_out(ticket_id, action, refund_id=None)
        except httpx.HTTPStatusError as e:
            logger.exception("Ticket %s: Stripe account credit failed", ticket_id)
            _log_event(
                ticket_id, "execution_failed", "execution", {"status_code": e.response.status_code, "response": e.response.text}
            )
            _set_status(ticket_id, "escalated")
            await _fail_out(ticket_id, f"Stripe error {e.response.status_code}: {e.response.text}")
        return

    if not action.target_transaction_id:
        # Defensive check, action_proposer's hallucination guard should already
        # have downgraded this before it ever reached "executing".
        logger.error(
            "Ticket %s: %s reached execution with no target_transaction_id, escalating", ticket_id, action.action_type
        )
        _log_event(ticket_id, "execution_failed", "execution", {"reason": "missing target_transaction_id"})
        _set_status(ticket_id, "escalated")
        await _fail_out(ticket_id, "Proposed action had no valid target transaction to refund.")
        return

    try:
        result = await _issue_refund(ticket_id, action.target_transaction_id, action.amount)
        logger.info(
            "Ticket %s: refund %s issued on %s (amount=%d)",
            ticket_id,
            result.get("id"),
            action.target_transaction_id,
            action.amount,
        )
        _log_event(
            ticket_id,
            "executed",
            "execution",
            {
                "refund_id": result.get("id"),
                "amount": action.amount,
                "target_transaction_id": action.target_transaction_id,
                "stripe_result": result,
            },
        )
        _set_status(ticket_id, "resolved")
        await _close_out(ticket_id, action, refund_id=result.get("id"))
    except httpx.HTTPStatusError as e:
        logger.exception("Ticket %s: Stripe refund failed", ticket_id)
        _log_event(
            ticket_id,
            "execution_failed",
            "execution",
            {"status_code": e.response.status_code, "response": e.response.text},
        )
        _set_status(ticket_id, "escalated")
        await _fail_out(ticket_id, f"Stripe error {e.response.status_code}: {e.response.text}")
