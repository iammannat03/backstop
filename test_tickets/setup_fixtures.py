"""Provisions the real Stripe test-mode data and real Zendesk tickets behind
the three demo scenarios documented in test_tickets/README.md. Run once ahead
of the demo (and again any time the Stripe/Zendesk test data gets wiped).

Deliberately NOT idempotent, re-running creates fresh customers/charges/
tickets rather than checking for existing ones. If you need a clean re-run,
delete the old customers/tickets first (this script prints their ids).

Usage: uv run python test_tickets/setup_fixtures.py
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.zendesk_auth import BASE_URL as ZENDESK_BASE_URL  # noqa: E402
from shared.zendesk_auth import authed_request  # noqa: E402

load_dotenv()

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_BASE_URL = "https://api.stripe.com/v1"


def _stripe_auth() -> tuple[str, str]:
    return (STRIPE_API_KEY, "")


async def create_customer(client: httpx.AsyncClient, email: str, name: str) -> str:
    resp = await client.post(
        f"{STRIPE_BASE_URL}/customers", data={"email": email, "name": name}, auth=_stripe_auth()
    )
    resp.raise_for_status()
    customer_id = resp.json()["id"]

    resp = await client.post(
        f"{STRIPE_BASE_URL}/customers/{customer_id}/sources", data={"source": "tok_visa"}, auth=_stripe_auth()
    )
    resp.raise_for_status()
    return customer_id


async def create_charge(client: httpx.AsyncClient, customer_id: str, amount: int, description: str) -> str:
    resp = await client.post(
        f"{STRIPE_BASE_URL}/charges",
        data={"amount": amount, "currency": "usd", "customer": customer_id, "description": description},
        auth=_stripe_auth(),
    )
    resp.raise_for_status()
    return resp.json()["id"]


async def create_zendesk_ticket(client: httpx.AsyncClient, subject: str, body: str, email: str, name: str) -> str:
    resp = await authed_request(
        client,
        "POST",
        f"{ZENDESK_BASE_URL}/tickets.json",
        json={"ticket": {"subject": subject, "comment": {"body": body}, "requester": {"email": email, "name": name}}},
    )
    return str(resp.json()["ticket"]["id"])


async def setup_scenario_1_clean_duplicate(stripe: httpx.AsyncClient, zendesk: httpx.AsyncClient) -> None:
    """Scenario 1: genuine duplicate charge. Should resolve end-to-end with
    zero human touch, proving the automation half of the pitch."""
    email = "billing@thornbury-logistics-demo.test"
    customer_id = await create_customer(stripe, email, "Thornbury Logistics")
    await create_charge(stripe, customer_id, 4500, "Monthly Platform Fee")
    charge2 = await create_charge(stripe, customer_id, 4500, "Monthly Platform Fee")
    zendesk_id = await create_zendesk_ticket(
        zendesk,
        "Duplicate charge on my account",
        "Hi, I just noticed I was charged $45.00 twice for this month's platform fee. "
        "Can you refund the duplicate charge?",
        email,
        "Thornbury Logistics",
    )
    print(f"[Scenario 1: clean duplicate] customer={customer_id} charge2={charge2} zendesk#{zendesk_id}")


async def setup_scenario_2_proration_mismatch(stripe: httpx.AsyncClient, zendesk: httpx.AsyncClient) -> None:
    """Scenario 2: a genuine Stripe-computed proration charge from a real
    subscription upgrade, but the ticket is written to sound like an
    unauthorized charge. The core disagreement demo moment."""
    email = "ap@ashgrove-media-demo.test"
    customer_id = await create_customer(stripe, email, "Ashgrove Media")

    product = await stripe.post(f"{STRIPE_BASE_URL}/products", data={"name": "Ashgrove Demo Plan"}, auth=_stripe_auth())
    product.raise_for_status()
    product_id = product.json()["id"]

    basic = await stripe.post(
        f"{STRIPE_BASE_URL}/prices",
        data={"product": product_id, "unit_amount": 2000, "currency": "usd", "recurring[interval]": "month", "nickname": "Basic"},
        auth=_stripe_auth(),
    )
    basic.raise_for_status()
    basic_price = basic.json()["id"]

    pro = await stripe.post(
        f"{STRIPE_BASE_URL}/prices",
        data={"product": product_id, "unit_amount": 5000, "currency": "usd", "recurring[interval]": "month", "nickname": "Pro"},
        auth=_stripe_auth(),
    )
    pro.raise_for_status()
    pro_price = pro.json()["id"]

    sub = await stripe.post(
        f"{STRIPE_BASE_URL}/subscriptions",
        data={"customer": customer_id, "items[0][price]": basic_price},
        auth=_stripe_auth(),
    )
    sub.raise_for_status()
    sub_id = sub.json()["id"]
    item_id = sub.json()["items"]["data"][0]["id"]

    upgrade = await stripe.post(
        f"{STRIPE_BASE_URL}/subscriptions/{sub_id}",
        data={"items[0][id]": item_id, "items[0][price]": pro_price, "proration_behavior": "always_invoice"},
        auth=_stripe_auth(),
    )
    upgrade.raise_for_status()

    zendesk_id = await create_zendesk_ticket(
        zendesk,
        "Unauthorized charge on my account",
        "I just noticed an extra $30 charge on our account that I did not authorize. "
        "This needs to be refunded immediately, please treat this as urgent.",
        email,
        "Ashgrove Media",
    )
    print(f"[Scenario 2: proration mismatch] customer={customer_id} subscription={sub_id} zendesk#{zendesk_id}")


async def setup_scenario_3_divergence(stripe: httpx.AsyncClient, zendesk: httpx.AsyncClient) -> None:
    """Scenario 3: two charges of the same amount, close together, but
    genuinely different purchases. Tests whether the system pattern-matches
    on amount and timing, or actually reads what each charge is for."""
    email = "finance@milldale-studio-demo.test"
    customer_id = await create_customer(stripe, email, "Milldale Studio")

    product = await stripe.post(f"{STRIPE_BASE_URL}/products", data={"name": "Milldale Pro Plan"}, auth=_stripe_auth())
    product.raise_for_status()
    product_id = product.json()["id"]

    price = await stripe.post(
        f"{STRIPE_BASE_URL}/prices",
        data={"product": product_id, "unit_amount": 8900, "currency": "usd", "recurring[interval]": "month"},
        auth=_stripe_auth(),
    )
    price.raise_for_status()
    price_id = price.json()["id"]

    sub = await stripe.post(
        f"{STRIPE_BASE_URL}/subscriptions",
        data={"customer": customer_id, "items[0][price]": price_id},
        auth=_stripe_auth(),
    )
    sub.raise_for_status()

    # A genuinely separate, one-time charge that happens to cost the same,
    # not part of the subscription/invoice at all.
    addon_charge = await create_charge(stripe, customer_id, 8900, "Additional Seat License (one-time)")

    zendesk_id = await create_zendesk_ticket(
        zendesk,
        "Possible duplicate charge",
        "I noticed what looks like the same $89 charge twice on my account this week, "
        "looks like a duplicate. Can you refund one of them?",
        email,
        "Milldale Studio",
    )
    print(f"[Scenario 3: divergence] customer={customer_id} addon_charge={addon_charge} zendesk#{zendesk_id}")


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as stripe, httpx.AsyncClient(timeout=30.0) as zendesk:
        await setup_scenario_1_clean_duplicate(stripe, zendesk)
        await setup_scenario_2_proration_mismatch(stripe, zendesk)
        await setup_scenario_3_divergence(stripe, zendesk)
    print("\nDone. These Zendesk tickets are real and unprocessed, trigger a poll "
          "(POST /ingest/poll-now, or wait for the next automatic poll) to run them live.")


if __name__ == "__main__":
    asyncio.run(main())
