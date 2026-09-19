"""AWS Lambda handler for Stripe action execution, the only module with
permission to actually move money. Fires only after both the OPA gate and
the verifier have cleared the action (upstream Step Functions states), and
trusts that gate completely, it does not re-derive or re-check anything.

Idempotency: every Stripe call carries an Idempotency-Key derived from the
ticket id and the action verb (backstop-refund-<ticket_id>,
backstop-cancel-<ticket_id>, backstop-credit-<ticket_id>). A Step Functions
retry that reinvokes this handler for the same ticket_id reissues the
identical key, so Stripe returns the original result instead of performing
the action twice. There is no separate DynamoDB pre-check for "already
executed", backstop-prac's original executor relied on Stripe-side
idempotency alone for this, and this port preserves that rather than
bolting on a check that was not there.

The Slack and Zendesk close-out that backstop-prac did inline at the end of
execute_action now happens in a separate audit step downstream in the Step
Functions state machine instead, this handler's job ends at recording the
outcome in DynamoDB and handing the result back for that next state to act
on. customer_facing_resolution_message stays here since it is pure data
shaping with no side effect, the audit step imports it from this module.
"""

import asyncio
import logging
import os

import httpx

from persistence.dynamo import append_audit_record, get_ticket, update_ticket_status
from shared.models import ProposedAction

logger = logging.getLogger("execution.execute_handler")

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_BASE_URL = "https://api.stripe.com/v1"

# Requires no Stripe call at all, resolving directly is the correct outcome.
_NO_STRIPE_CALL_NEEDED = {"no_action"}
_EXECUTABLE_ACTION_TYPES = {"refund", "partial_refund", "cancel_subscription", "apply_account_credit"}


def _auth() -> tuple[str, str]:
    return (STRIPE_API_KEY, "")


async def _cancel_subscription(ticket_id: str, subscription_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(
            f"{STRIPE_BASE_URL}/subscriptions/{subscription_id}",
            headers={"Idempotency-Key": f"backstop-cancel-{ticket_id}"},
            auth=_auth(),
        )
        resp.raise_for_status()
        return resp.json()


async def _apply_account_credit(ticket_id: str, customer_id: str, amount: int, currency: str) -> dict:
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


async def _issue_refund(ticket_id: str, charge_id: str, amount: int) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{STRIPE_BASE_URL}/refunds",
            data={"charge": charge_id, "amount": amount},
            headers={"Idempotency-Key": f"backstop-refund-{ticket_id}"},
            auth=_auth(),
        )
        resp.raise_for_status()
        return resp.json()


def customer_facing_resolution_message(action: ProposedAction) -> str:
    # A human @backstop command can dictate the exact customer-facing wording;
    # the worker and verifier never set this, so it's always a human's choice.
    if action.customer_message:
        return action.customer_message
    if action.action_type in ("refund", "partial_refund"):
        return f"We've reviewed your account and issued a refund of ${action.amount / 100:.2f} for this charge."
    if action.action_type == "cancel_subscription":
        return "Your subscription has been cancelled as requested."
    if action.action_type == "apply_account_credit":
        return f"We've applied a ${action.amount / 100:.2f} credit to your account balance."
    if action.action_type == "flag_for_fraud_review":
        # Must never claim "no action needed" or otherwise confirm/deny
        # anything, this is a security matter for a human to handle, not
        # something the customer should be told is already resolved.
        return (
            "Thank you for reaching out. We've flagged this for review by our security team, "
            "and will follow up separately if we need any additional information from you."
        )
    # "no_action" covers both a charge that turned out to be correct and a
    # ticket that was never about billing at all, so this stays generic.
    return "Thanks for reaching out. After review, no billing action was needed on this ticket."


async def _execute_action(ticket_id: str, action: ProposedAction, customer_id: str | None) -> dict:
    if action.action_type in _NO_STRIPE_CALL_NEEDED:
        logger.info(
            "Ticket %s: action_type=%s requires no Stripe call, resolving directly", ticket_id, action.action_type
        )
        append_audit_record(ticket_id, "executed", "execution", {"action_type": action.action_type, "stripe_result": None})
        update_ticket_status(ticket_id, "resolved")
        return {"ticket_id": ticket_id, "status": "resolved", "action_type": action.action_type, "stripe_result": None}

    if action.action_type not in _EXECUTABLE_ACTION_TYPES:
        # No branch for this action_type. Escalate instead of silently
        # marking it resolved with no real-world effect.
        logger.error(
            "Ticket %s: no execution handler for action_type=%s, escalating instead of silently resolving",
            ticket_id,
            action.action_type,
        )
        append_audit_record(
            ticket_id, "execution_failed", "execution", {"reason": "no handler for action_type", "action_type": action.action_type}
        )
        update_ticket_status(ticket_id, "escalated")
        return {
            "ticket_id": ticket_id,
            "status": "escalated",
            "action_type": action.action_type,
            "error": f"No execution handler exists yet for action_type={action.action_type!r}.",
        }

    if action.action_type == "cancel_subscription":
        if not action.target_subscription_id:
            logger.error("Ticket %s: cancel_subscription reached execution with no target_subscription_id", ticket_id)
            append_audit_record(ticket_id, "execution_failed", "execution", {"reason": "missing target_subscription_id"})
            update_ticket_status(ticket_id, "escalated")
            return {
                "ticket_id": ticket_id,
                "status": "escalated",
                "action_type": action.action_type,
                "error": "Proposed action had no valid target subscription to cancel.",
            }
        try:
            result = await _cancel_subscription(ticket_id, action.target_subscription_id)
            logger.info("Ticket %s: subscription %s cancelled", ticket_id, action.target_subscription_id)
            append_audit_record(
                ticket_id,
                "executed",
                "execution",
                {"target_subscription_id": action.target_subscription_id, "stripe_result": result},
            )
            update_ticket_status(ticket_id, "resolved")
            return {"ticket_id": ticket_id, "status": "resolved", "action_type": action.action_type, "stripe_result": result}
        except httpx.HTTPStatusError as e:
            logger.exception("Ticket %s: Stripe subscription cancellation failed", ticket_id)
            append_audit_record(
                ticket_id, "execution_failed", "execution", {"status_code": e.response.status_code, "response": e.response.text}
            )
            update_ticket_status(ticket_id, "escalated")
            return {
                "ticket_id": ticket_id,
                "status": "escalated",
                "action_type": action.action_type,
                "error": f"Stripe error {e.response.status_code}: {e.response.text}",
            }

    if action.action_type == "apply_account_credit":
        if not customer_id:
            logger.error("Ticket %s: apply_account_credit reached execution with no known Stripe customer", ticket_id)
            append_audit_record(ticket_id, "execution_failed", "execution", {"reason": "missing customer_id"})
            update_ticket_status(ticket_id, "escalated")
            return {
                "ticket_id": ticket_id,
                "status": "escalated",
                "action_type": action.action_type,
                "error": "Proposed action had no known Stripe customer to credit.",
            }
        try:
            result = await _apply_account_credit(ticket_id, customer_id, action.amount, action.currency)
            logger.info("Ticket %s: credited %d %s to customer %s", ticket_id, action.amount, action.currency, customer_id)
            append_audit_record(
                ticket_id,
                "executed",
                "execution",
                {"customer_id": customer_id, "amount": action.amount, "currency": action.currency, "stripe_result": result},
            )
            update_ticket_status(ticket_id, "resolved")
            return {"ticket_id": ticket_id, "status": "resolved", "action_type": action.action_type, "stripe_result": result}
        except httpx.HTTPStatusError as e:
            logger.exception("Ticket %s: Stripe account credit failed", ticket_id)
            append_audit_record(
                ticket_id, "execution_failed", "execution", {"status_code": e.response.status_code, "response": e.response.text}
            )
            update_ticket_status(ticket_id, "escalated")
            return {
                "ticket_id": ticket_id,
                "status": "escalated",
                "action_type": action.action_type,
                "error": f"Stripe error {e.response.status_code}: {e.response.text}",
            }

    if not action.target_transaction_id:
        # Defensive check, action_proposer's hallucination guard should already
        # have downgraded this before it ever reached execution.
        logger.error(
            "Ticket %s: %s reached execution with no target_transaction_id, escalating", ticket_id, action.action_type
        )
        append_audit_record(ticket_id, "execution_failed", "execution", {"reason": "missing target_transaction_id"})
        update_ticket_status(ticket_id, "escalated")
        return {
            "ticket_id": ticket_id,
            "status": "escalated",
            "action_type": action.action_type,
            "error": "Proposed action had no valid target transaction to refund.",
        }

    try:
        result = await _issue_refund(ticket_id, action.target_transaction_id, action.amount)
        logger.info(
            "Ticket %s: refund %s issued on %s (amount=%d)",
            ticket_id,
            result.get("id"),
            action.target_transaction_id,
            action.amount,
        )
        append_audit_record(
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
        update_ticket_status(ticket_id, "resolved")
        return {
            "ticket_id": ticket_id,
            "status": "resolved",
            "action_type": action.action_type,
            "stripe_result": result,
            "refund_id": result.get("id"),
        }
    except httpx.HTTPStatusError as e:
        logger.exception("Ticket %s: Stripe refund failed", ticket_id)
        append_audit_record(
            ticket_id,
            "execution_failed",
            "execution",
            {"status_code": e.response.status_code, "response": e.response.text},
        )
        update_ticket_status(ticket_id, "escalated")
        return {
            "ticket_id": ticket_id,
            "status": "escalated",
            "action_type": action.action_type,
            "error": f"Stripe error {e.response.status_code}: {e.response.text}",
        }


def lambda_handler(event, context):
    """event: {"ticket_id": "<uuid string>"}. Reads the ticket's already
    proposed_action off DynamoDB (worker pipeline sets it), executes it
    against Stripe, and returns a small dict a Step Functions Choice state
    downstream (the audit branch) can act on."""
    ticket_id = event["ticket_id"]
    ticket = get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"No ticket found for ticket_id={ticket_id!r}")
    proposed_action = ticket.get("proposed_action")
    if not proposed_action:
        raise ValueError(f"Ticket {ticket_id} reached execution with no proposed_action recorded")
    action = ProposedAction(**proposed_action)
    customer_id = ticket.get("customer_id")
    return asyncio.run(_execute_action(ticket_id, action, customer_id))
