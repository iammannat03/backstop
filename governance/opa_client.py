"""Python glue for the OPA policy gate. Runs `opa eval` as an async subprocess
so it yields control back to the event loop for other tickets' processing.

In a Lambda execution environment the opa binary ships as a Lambda layer,
whose contents get mounted read only at /opt. OPA_BINARY_PATH lets the
handler's environment variables point at the exact mounted path, defaulting
to the conventional layer bin/ location. Outside Lambda (a local test run,
or opa on PATH from a system install) it falls back to plain "opa" so the
same code runs unmodified in both places.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from shared.models import ProposedAction, Subscription, Transaction

logger = logging.getLogger("governance.opa_client")

GOVERNANCE_DIR = Path(__file__).resolve().parent

OPA_BINARY_PATH = os.getenv("OPA_BINARY_PATH", "/opt/bin/opa")


def _opa_binary() -> str:
    """Uses the configured Lambda layer path if it actually exists on disk,
    otherwise falls back to "opa" on PATH for local runs outside Lambda."""
    if Path(OPA_BINARY_PATH).exists():
        return OPA_BINARY_PATH
    return "opa"


def build_opa_input(
    action: ProposedAction,
    customer_id: str | None,
    refunds_last_30_days: int,
    ticket_count_last_90_days: int,
    has_fraud_flag: bool,
    transaction: Transaction | None,
    subscription: Subscription | None,
) -> dict:
    """Assembles the `input` document OPA's rules expect."""
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
        _opa_binary(),
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
