"""Python glue for the OPA policy gate built in phase 2 (governance/*.rego,
verified via `opa test`, 11/11 passing). Not part of architecture.md's listed
governance/ files, but necessary: the Rego policies can't call themselves,
something has to hand them a `ProposedAction` and read back the decision.

Runs `opa eval` as an async subprocess rather than a blocking call, consistent
with the rest of the pipeline: even though the call itself is fast, this still
yields control back to the event loop for other tickets' concurrent processing.
"""

import asyncio
import json
import logging
from pathlib import Path

from shared.models import ProposedAction, Subscription, Transaction

logger = logging.getLogger("governance.opa_client")

GOVERNANCE_DIR = Path(__file__).resolve().parent


def build_opa_input(
    action: ProposedAction,
    customer_id: str | None,
    refunds_last_30_days: int,
    ticket_count_last_90_days: int,
    has_fraud_flag: bool,
    transaction: Transaction | None,
    subscription: Subscription | None,
) -> dict:
    """Assembles the `input` document OPA's rules expect (docs/rules.md's "OPA
    policy input schema" section) from a ProposedAction plus the customer/
    transaction/subscription facts it needs to check against. Shared by
    verifier_agent/pipeline.py (auto-pipeline) and command_agent/pipeline.py
    (human `@backstop` commands), both hand OPA the same fact shape regardless
    of who proposed the action, since OPA's deterministic limits apply
    identically either way."""
    return {
        "action": {
            "action_type": action.action_type,
            "amount": action.amount,
            "currency": action.currency,
            "target_transaction_id": action.target_transaction_id,
            "target_subscription_id": action.target_subscription_id,
            "confidence": action.confidence,
        },
        "customer": {
            "id": customer_id or "",
            "has_fraud_flag": has_fraud_flag,
            "refunds_last_30_days": refunds_last_30_days,
            "ticket_count_last_90_days": ticket_count_last_90_days,
        },
        "transaction": {
            "id": transaction.id if transaction else (action.target_transaction_id or ""),
            "amount": transaction.amount if transaction else 0,
            "currency": transaction.currency if transaction else action.currency,
            "is_disputed": transaction.disputed if transaction else False,
        },
        "subscription": {
            "id": subscription.id if subscription else (action.target_subscription_id or ""),
            "status": subscription.status if subscription else "",
        },
    }


async def evaluate_policy(opa_input: dict) -> dict:
    """Returns the {decision, matched_rule, reason, all_matched_rules} object
    from governance/main.rego's `result` rule."""
    proc = await asyncio.create_subprocess_exec(
        "opa",
        "eval",
        "-d",
        str(GOVERNANCE_DIR),
        "-I",
        "--format",
        "json",
        "data.backstop.governance.result",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=json.dumps(opa_input).encode())

    if proc.returncode != 0:
        raise RuntimeError(f"opa eval failed (exit {proc.returncode}): {stderr.decode()}")

    data = json.loads(stdout)
    return data["result"][0]["expressions"][0]["value"]
