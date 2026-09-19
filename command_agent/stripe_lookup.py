"""A fresh Stripe pull for the command path, deliberately self contained
rather than importing worker_agent's investigator.

Each Lambda in this port owns its own Stripe read for the same reason
backstop-prac's verifier re-pulled independently instead of reusing the
worker's copy: this keeps command_handler deployable and testable as one
unit without a cross-module dependency on a sibling Lambda's package. It
skips proration mapping (the invoices fetch and its cross-reference against
charges) since command_parser's prompt never needed proration detail, only
transaction and subscription facts, and Transaction.is_proration /
proration_details already default to false/None in shared.models when left
unset. A later cleanup pass could pull the shared parts of this and
worker_agent/stripe_investigator.py into one place if that duplication
becomes a maintenance problem, out of scope here.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from shared.models import StripeHistory, Subscription, Transaction

load_dotenv()

logger = logging.getLogger("command_agent.stripe_lookup")

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_BASE_URL = "https://api.stripe.com/v1"


def _auth() -> tuple[str, str]:
    return (STRIPE_API_KEY, "")


async def _find_customer(client: httpx.AsyncClient, email: str) -> dict | None:
    resp = await client.get(f"{STRIPE_BASE_URL}/customers", params={"email": email, "limit": 1}, auth=_auth())
    resp.raise_for_status()
    customers = resp.json().get("data", [])
    return customers[0] if customers else None


async def _fetch_charges(client: httpx.AsyncClient, customer_id: str) -> list[dict]:
    resp = await client.get(
        f"{STRIPE_BASE_URL}/charges", params={"customer": customer_id, "limit": 100}, auth=_auth()
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


async def _fetch_subscriptions(client: httpx.AsyncClient, customer_id: str) -> list[dict]:
    resp = await client.get(
        f"{STRIPE_BASE_URL}/subscriptions",
        params={"customer": customer_id, "limit": 100, "status": "all"},
        auth=_auth(),
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _plan_nickname(sub: dict) -> str | None:
    plan = sub.get("plan") or {}
    if plan.get("nickname"):
        return plan["nickname"]
    items = (sub.get("items") or {}).get("data") or []
    if items:
        return (items[0].get("price") or {}).get("nickname")
    return None


def _current_period(sub: dict) -> tuple[int, int]:
    items = (sub.get("items") or {}).get("data") or []
    if items:
        return items[0]["current_period_start"], items[0]["current_period_end"]
    fallback = sub.get("start_date", 0)
    return fallback, fallback


async def investigate_customer(customer_email: str) -> StripeHistory:
    """Returns an empty-but-valid StripeHistory if no Stripe customer matches
    this email, rather than raising, mirrors worker_agent's investigator."""
    if not STRIPE_API_KEY:
        raise RuntimeError("STRIPE_API_KEY not set")

    async with httpx.AsyncClient(timeout=30.0) as client:
        customer = await _find_customer(client, customer_email)
        if customer is None:
            logger.info("No Stripe customer found for email %s", customer_email)
            return StripeHistory(customer_id=None)

        customer_id = customer["id"]
        charges, subscriptions = await asyncio.gather(
            _fetch_charges(client, customer_id),
            _fetch_subscriptions(client, customer_id),
        )

    transactions = [
        Transaction(
            id=c["id"],
            amount=c["amount"],
            currency=c["currency"],
            description=c.get("description"),
            created=datetime.fromtimestamp(c["created"], tz=timezone.utc),
            refunded=c["refunded"],
            amount_refunded=c["amount_refunded"],
            disputed=bool(c.get("disputed", False)),
            invoice_id=c.get("invoice"),
        )
        for c in charges
    ]

    subs = []
    for s in subscriptions:
        period_start, period_end = _current_period(s)
        subs.append(
            Subscription(
                id=s["id"],
                status=s["status"],
                plan_nickname=_plan_nickname(s),
                current_period_start=datetime.fromtimestamp(period_start, tz=timezone.utc),
                current_period_end=datetime.fromtimestamp(period_end, tz=timezone.utc),
            )
        )

    thirty_days_ago_ts = datetime.now(tz=timezone.utc).timestamp() - 30 * 86400
    refunds_last_30_days = sum(1 for c in charges if c["amount_refunded"] > 0 and c["created"] >= thirty_days_ago_ts)

    return StripeHistory(
        customer_id=customer_id,
        customer_metadata={
            "email": customer.get("email"),
            "created": customer.get("created"),
            **(customer.get("metadata") or {}),
        },
        transactions=transactions,
        subscriptions=subs,
        refunds_last_30_days=refunds_last_30_days,
    )
