"""Lambda entry point for the OPA policy gate step of the main pipeline
state machine, sits between propose_handler and verify_handler.

Input event: {"ticket_id": str, "proposed_action": dict}
Output: {"ticket_id": str, "decision": "allow" | "deny" | "escalate",
         "matched_rule": str | None, "reason": str | None}

Mirrors what backstop-prac's verifier_agent/pipeline.py did before ever
calling the verifier: re-pulls Stripe fresh for this customer (yes, this is
a second pull within the same execution for a billing-relevant ticket,
investigate_handler already pulled once earlier, that duplication is in the
original design and is preserved here rather than optimized away), builds
the OPA input, evaluates it, and persists the decision. A Step Functions
Choice state branches on decision == "allow" continuing to verify_handler,
anything else (deny or escalate) going straight to audit_handler with
outcome "policy_blocked". This matches backstop-prac's
`if policy_result["decision"] != "allow"` check exactly, both deny and
escalate block here. That is different from the human command path in
command_agent/command_handler.py, where only deny blocks and escalate
means "ask the human to clarify" instead, since a human already is the
approving party escalate would otherwise route to.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key

from governance.opa_client import build_opa_input, evaluate_policy
from persistence import dynamo
from shared.models import ProposedAction
from worker_agent.stripe_investigator import investigate_customer

logger = logging.getLogger("governance.policy_handler")

_ALL_TICKETS_GSI2PK = "TICKET"


def _count_recent_tickets(customer_email: str, days: int = 90) -> int:
    """Same GSI-based exact-match approach as
    command_agent.command_handler.count_recent_tickets, not
    verifier_agent.verify_handler's list_tickets-based one. Three
    near-identical implementations of this helper now exist across the
    repo (here, command_agent, verifier_agent), each written independently
    while porting its own module. Worth consolidating into one shared
    persistence helper later, out of scope for this phase."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    count = 0
    kwargs = {
        "IndexName": dynamo.ALL_TICKETS_INDEX,
        "KeyConditionExpression": Key("GSI2PK").eq(_ALL_TICKETS_GSI2PK) & Key("GSI2SK").gte(cutoff),
    }
    while True:
        resp = dynamo.get_table().query(**kwargs)
        count += sum(1 for item in resp.get("Items", []) if item.get("customer_email") == customer_email)
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return count


async def _evaluate(ticket_id: str, proposed_action: dict) -> dict:
    ticket = dynamo.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} not found")

    action = ProposedAction.model_validate(proposed_action)
    customer_email = ticket["customer_email"]

    stripe_history = await investigate_customer(customer_email)
    ticket_count_last_90_days = _count_recent_tickets(customer_email)
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

    dynamo.write_policy_decision(
        ticket_id,
        policy_result["decision"],
        policy_result["matched_rule"],
        policy_result["reason"],
        opa_input,
        policy_result,
    )
    dynamo.append_audit_record(ticket_id, "policy_decision", "opa", policy_result)

    if policy_result["decision"] != "allow":
        logger.info(
            "Ticket %s %s by policy: %s", ticket_id, policy_result["decision"], policy_result["matched_rule"]
        )
        dynamo.append_audit_record(
            ticket_id,
            "blocked",
            "opa",
            {
                "decision": policy_result["decision"],
                "matched_rule": policy_result["matched_rule"],
                "reason": policy_result["reason"],
            },
        )
        dynamo.update_ticket_status(ticket_id, "escalated")

    return {
        "ticket_id": ticket_id,
        "decision": policy_result["decision"],
        "matched_rule": policy_result["matched_rule"],
        "reason": policy_result["reason"],
    }


def lambda_handler(event: dict, context) -> dict:
    ticket_id = event["ticket_id"]
    proposed_action = event["proposed_action"]
    return asyncio.run(_evaluate(ticket_id, proposed_action))
