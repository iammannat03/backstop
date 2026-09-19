"""Action Proposer: packages the reasoning engine's decision into a
structured, auditable ProposedAction and persists it. Never executes
anything directly.

Validates the reasoning engine's claim against the real Stripe data it was
given before persisting: a refund can't target a transaction that doesn't
exist in this customer's history, or exceed what's actually refundable.
"""

import logging

from persistence import dynamo
from shared.models import ProposedAction, StripeHistory

logger = logging.getLogger("worker_agent.action_proposer")


def _downgrade_to_escalate(action: ProposedAction, reason: str) -> ProposedAction:
    return action.model_copy(
        update={
            "action_type": "escalate",
            "amount": 0,
            "target_transaction_id": None,
            "target_subscription_id": None,
            "rationale": f"{action.rationale}\n\n[action_proposer] Downgraded to escalate: {reason}",
        }
    )


def validate_against_stripe(action: ProposedAction, stripe_history: StripeHistory) -> ProposedAction:
    if action.action_type in ("refund", "partial_refund"):
        transaction = next((t for t in stripe_history.transactions if t.id == action.target_transaction_id), None)
        if transaction is None:
            logger.warning(
                "Reasoning engine proposed %s against unknown transaction_id=%r, downgrading to escalate",
                action.action_type,
                action.target_transaction_id,
            )
            return _downgrade_to_escalate(
                action,
                f"proposed target_transaction_id {action.target_transaction_id!r} was not found in this "
                f"customer's actual Stripe transaction history.",
            )

        refundable = transaction.amount - transaction.amount_refunded
        if action.amount > refundable:
            logger.warning(
                "Reasoning engine proposed refunding %d but only %d is refundable on %s, downgrading to escalate",
                action.amount,
                refundable,
                transaction.id,
            )
            return _downgrade_to_escalate(
                action,
                f"proposed refund amount {action.amount} exceeds the {refundable} actually refundable on "
                f"transaction {transaction.id}.",
            )

        return action

    if action.action_type == "cancel_subscription":
        subscription = next((s for s in stripe_history.subscriptions if s.id == action.target_subscription_id), None)
        if subscription is None:
            logger.warning(
                "Reasoning engine proposed cancel_subscription against unknown subscription_id=%r, "
                "downgrading to escalate",
                action.target_subscription_id,
            )
            return _downgrade_to_escalate(
                action,
                f"proposed target_subscription_id {action.target_subscription_id!r} was not found in this "
                f"customer's actual Stripe subscriptions.",
            )
        return action

    if action.action_type == "apply_account_credit":
        if not stripe_history.customer_id:
            logger.warning(
                "Reasoning engine proposed apply_account_credit with no known Stripe customer, downgrading to escalate"
            )
            return _downgrade_to_escalate(
                action, "proposed apply_account_credit but no Stripe customer was found for this ticket's email."
            )
        return action

    # no_action, escalate, flag_for_fraud_review: nothing here is grounded in
    # specific Stripe records to check against.
    return action


def propose_action(ticket_id: str, raw_action: ProposedAction, stripe_history: StripeHistory) -> ProposedAction:
    validated = validate_against_stripe(raw_action, stripe_history)

    dynamo.set_proposed_action(
        ticket_id,
        proposed_action=validated.model_dump(mode="json"),
        status="proposed_action",
        customer_id=stripe_history.customer_id,
        event_type="proposed_action",
        actor="worker_agent",
        event_detail=validated.model_dump(mode="json"),
    )
    logger.info("Persisted proposed_action for ticket %s: %s", ticket_id, validated.action_type)

    return validated
