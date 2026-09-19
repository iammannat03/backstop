"""Lambda entry point for the human command path: a support agent replying
"@backstop <command>" in a ticket's Slack thread.

A deliberately shorter chain than the automated worker pipeline: OPA still
applies in full, but there is no independent verifier re-derivation, there
is nothing to re-derive against once a human has explicitly stated the
action. A "deny" still blocks even a human command. An "escalate" or
"flag_for_fraud_review" outcome does not block execution here, since a
human is already the approving party those flags would otherwise route to,
it instead means the parser could not ground the command in a concrete
action and the human needs to clarify.

Expected event shape, this is the contract the future Slack events Lambda
(built in the ingestion phase) must hand off:

    {
        "ticket_id": "<uuid string, an existing ticket>",
        "raw_command": "<text typed after @backstop>",
        "slack_user_id": "<Slack user id of whoever typed the command>",
        "slack_channel": "<Slack channel id, carried through for the
            notification step a later audit phase builds, not used here>"
    }

Response shape, one of three outcomes a Step Functions Choice state (built
in the orchestration phase) branches on:

    {"outcome": "blocked", "ticket_id": ..., "matched_rule": ..., "reason": ...}
    {"outcome": "needs_clarification", "ticket_id": ..., "command_text": ..., "rationale": ...}
    {"outcome": "ready_to_execute", "ticket_id": ..., "action_type": ...}

"ready_to_execute" means the proposed action and policy decision are
already persisted and the ticket status is set to "executing", the next
state machine step invokes execution's execute_handler with the same
ticket_id. This handler never calls Stripe itself.
"""

import asyncio
import logging

from governance.opa_client import build_opa_input, evaluate_policy
from persistence.dynamo import (
    append_audit_record,
    get_table,
    set_proposed_action,
    write_policy_decision,
)
from shared.models import ProposedAction, StripeHistory

from command_agent.command_parser import parse_command
from command_agent.stripe_lookup import investigate_customer

logger = logging.getLogger("command_agent.command_handler")

# Same shape as the verifier's NEVER_AUTO_EXECUTE in backstop-prac: these
# mean "not enough grounding to act," so the command needs the human to
# clarify rather than being silently blocked or silently executed.
_NEEDS_CLARIFICATION = {"escalate", "flag_for_fraud_review"}

_ALL_TICKETS_GSI2PK = "TICKET"


def _downgrade_to_escalate(action: ProposedAction, reason: str) -> ProposedAction:
    return action.model_copy(
        update={
            "action_type": "escalate",
            "amount": 0,
            "target_transaction_id": None,
            "target_subscription_id": None,
            "rationale": f"{action.rationale}\n\n[command_handler] Downgraded to escalate: {reason}",
        }
    )


def validate_against_stripe(action: ProposedAction, stripe_history: StripeHistory) -> ProposedAction:
    """Same grounding check worker_agent's action_proposer runs on the
    reasoning engine's output, applied here to the parsed human command. A
    human can still ask for something that doesn't match the customer's
    actual Stripe records, and this must not be trusted blindly either."""
    if action.action_type in ("refund", "partial_refund"):
        transaction = next((t for t in stripe_history.transactions if t.id == action.target_transaction_id), None)
        if transaction is None:
            logger.warning(
                "Command proposed %s against unknown transaction_id=%r, downgrading to escalate",
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
                "Command proposed refunding %d but only %d is refundable on %s, downgrading to escalate",
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
                "Command proposed cancel_subscription against unknown subscription_id=%r, downgrading to escalate",
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
            logger.warning("Command proposed apply_account_credit with no known Stripe customer, downgrading")
            return _downgrade_to_escalate(
                action, "proposed apply_account_credit but no Stripe customer was found for this ticket's email."
            )
        return action

    # no_action, escalate, flag_for_fraud_review: nothing here is grounded
    # in specific Stripe records to check against.
    return action


def count_recent_tickets(customer_email: str, days: int = 90) -> int:
    """The customer's past ticket pattern, also fed into the OPA input,
    same 90 day window backstop-prac's verifier used. Queries the
    AllTicketsIndex GSI directly rather than going through
    persistence.dynamo.list_tickets, which only offers a substring match on
    customer_email, this needs an exact match for a count that feeds policy
    decisions."""
    from datetime import datetime, timedelta, timezone

    from boto3.dynamodb.conditions import Key

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    count = 0
    kwargs = {
        "IndexName": "AllTicketsIndex",
        "KeyConditionExpression": Key("GSI2PK").eq(_ALL_TICKETS_GSI2PK) & Key("GSI2SK").gte(cutoff),
    }
    while True:
        resp = get_table().query(**kwargs)
        count += sum(1 for item in resp.get("Items", []) if item.get("customer_email") == customer_email)
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return count


async def _run(ticket_id: str, raw_command: str, slack_user_id: str) -> dict:
    from persistence.dynamo import get_ticket

    actor = f"human:{slack_user_id}"
    ticket = get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} not found")

    append_audit_record(ticket_id, "command_received", actor, {"command_text": raw_command})

    stripe_history = await investigate_customer(ticket["customer_email"])
    raw_action = await parse_command(raw_command, ticket["ticket_text"], stripe_history)
    append_audit_record(ticket_id, "command_parsed", actor, raw_action.model_dump(mode="json"))

    action = validate_against_stripe(raw_action, stripe_history)
    if action is not raw_action:
        append_audit_record(ticket_id, "command_downgraded", actor, action.model_dump(mode="json"))

    ticket_count_last_90_days = count_recent_tickets(ticket["customer_email"])
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
    write_policy_decision(
        ticket_id,
        policy_result["decision"],
        policy_result["matched_rule"],
        policy_result["reason"],
        opa_input,
        policy_result,
    )
    append_audit_record(ticket_id, "command_policy_decision", actor, policy_result)

    if policy_result["decision"] == "deny":
        logger.info(
            "Ticket %s: command %r denied by policy: %s", ticket_id, raw_command, policy_result["matched_rule"]
        )
        return {
            "outcome": "blocked",
            "ticket_id": ticket_id,
            "command_text": raw_command,
            "matched_rule": policy_result["matched_rule"],
            "reason": policy_result["reason"],
        }

    if action.action_type in _NEEDS_CLARIFICATION:
        logger.info(
            "Ticket %s: command %r could not be grounded in a concrete action, asking for clarification",
            ticket_id,
            raw_command,
        )
        return {
            "outcome": "needs_clarification",
            "ticket_id": ticket_id,
            "command_text": raw_command,
            "rationale": action.rationale,
        }

    logger.info("Ticket %s: human command %r ready to execute (%s)", ticket_id, raw_command, action.action_type)
    set_proposed_action(
        ticket_id,
        action.model_dump(mode="json"),
        status="executing",
        customer_id=stripe_history.customer_id,
        event_type="command_proposed_action",
        actor=actor,
        event_detail=action.model_dump(mode="json"),
    )
    return {"outcome": "ready_to_execute", "ticket_id": ticket_id, "action_type": action.action_type}


def lambda_handler(event, context):
    ticket_id = event["ticket_id"]
    raw_command = event["raw_command"]
    slack_user_id = event["slack_user_id"]
    return asyncio.run(_run(ticket_id, raw_command, slack_user_id))
