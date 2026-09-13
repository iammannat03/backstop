"""Stripe Investigator: pulls ground-truth transaction and subscription
history before any reasoning happens. Matches a ticket to a Stripe customer
by email, since ingestion only captures the requester's email.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from shared.models import StripeHistory, Subscription, Transaction

load_dotenv()

logger = logging.getLogger("worker_agent.stripe_investigator")

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


async def _fetch_invoices(client: httpx.AsyncClient, customer_id: str) -> list[dict]:
    # expand[]=data.payments (the list endpoint rejects plain "payments") to
    # get invoice.payments[].payment.payment_intent for proration matching.
    resp = await client.get(
        f"{STRIPE_BASE_URL}/invoices",
        params={"customer": customer_id, "limit": 100, "expand[]": "data.payments"},
        auth=_auth(),
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


def _build_proration_map(invoices: list[dict]) -> dict[str, str]:
    """payment_intent_id to human-readable proration description. Neither
    invoice.charge nor charge.invoice exist on this API version, so the only
    cross-reference is invoice.payments[].payment.payment_intent matched
    against charge.payment_intent; the proration flag itself lives at
    line.parent.subscription_item_details.proration."""
    proration_by_payment_intent: dict[str, str] = {}
    for invoice in invoices:
        payment_intents = [
            (p.get("payment") or {}).get("payment_intent")
            for p in (invoice.get("payments") or {}).get("data", [])
        ]
        payment_intents = [pi for pi in payment_intents if pi]
        if not payment_intents:
            continue

        lines = (invoice.get("lines") or {}).get("data", [])
        proration_lines = [
            line
            for line in lines
            if ((line.get("parent") or {}).get("subscription_item_details") or {}).get("proration")
        ]
        if not proration_lines:
            continue

        detail = "; ".join(line.get("description") or "Proration adjustment" for line in proration_lines)
        for pi in payment_intents:
            proration_by_payment_intent[pi] = detail
    return proration_by_payment_intent


def _plan_nickname(sub: dict) -> str | None:
    plan = sub.get("plan") or {}
    if plan.get("nickname"):
        return plan["nickname"]
    items = (sub.get("items") or {}).get("data") or []
    if items:
        return (items[0].get("price") or {}).get("nickname")
    return None


def _current_period(sub: dict) -> tuple[int, int]:
    """current_period_start/end live on the first subscription item, not the
    subscription itself. Falls back to start_date if items are empty."""
    items = (sub.get("items") or {}).get("data") or []
    if items:
        return items[0]["current_period_start"], items[0]["current_period_end"]
    fallback = sub.get("start_date", 0)
    return fallback, fallback


async def investigate_customer(customer_email: str) -> StripeHistory:
    """Returns an empty-but-valid StripeHistory if no Stripe customer matches
    this email, rather than raising; that's a meaningful result, not an error."""
    if not STRIPE_API_KEY:
        raise RuntimeError("STRIPE_API_KEY not set in .env")

    async with httpx.AsyncClient(timeout=30.0) as client:
        customer = await _find_customer(client, customer_email)
        if customer is None:
            logger.info("No Stripe customer found for email %s", customer_email)
            return StripeHistory(customer_id=None)

        customer_id = customer["id"]
        charges, invoices, subscriptions = await asyncio.gather(
            _fetch_charges(client, customer_id),
            _fetch_invoices(client, customer_id),
            _fetch_subscriptions(client, customer_id),
        )

    proration_by_payment_intent = _build_proration_map(invoices)

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
            is_proration=c.get("payment_intent") in proration_by_payment_intent,
            proration_details=proration_by_payment_intent.get(c.get("payment_intent")),
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
