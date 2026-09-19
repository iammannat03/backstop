"""Zendesk poller Lambda, ported from ZendeskPoller in backstop-prac's
ingestion/zendesk_webhook.py (poll_once and process_ticket).

Cadence: the original polled every 15 seconds in a long-lived asyncio loop.
EventBridge cannot schedule below one minute, so the schedule fires once per
minute and each invocation polls POLLS_PER_INVOCATION (4) times, spaced
ZENDESK_POLL_INTERVAL_SECONDS (15) apart from the start of the invocation.
That keeps the 15 second cadence with a Timeout of about 65 seconds. Polls
are skipped, not delayed, if the remaining time would not cover another one.

Overlap: an invocation holds a DynamoDB lease for its whole run. A second
invocation (a slow previous minute, or the poll-now route) that cannot get the
lease returns immediately, so two pollers never read the same cursor and race
to create the same ticket.

Cursor semantics match the original: the first ever poll starts from "now
minus 90 seconds" (Zendesk needs start_time at least a minute old), every
response's after_cursor is used for the next call, and each poll drains pages
until end_of_stream. The cursor is stored in DynamoDB, and is saved after a
page's tickets are handled rather than before, so an invocation killed midway
re-reads that page instead of losing it (already ingested tickets are skipped
by the duplicate check).
"""

import asyncio
import logging
import os
import time
import uuid

import boto3
import httpx

from ingestion import state_store, zendesk_tokens
from persistence import dynamo
from shared import zendesk_auth as za

logger = logging.getLogger("ingestion.poll_handler")

POLLS_PER_INVOCATION = int(os.getenv("POLLS_PER_INVOCATION", "4"))
POLL_INTERVAL_SECONDS = float(os.getenv("ZENDESK_POLL_INTERVAL_SECONDS", "15"))
LEASE_SECONDS = int(os.getenv("POLL_LEASE_SECONDS", "70"))
POLL_LEASE_NAME = "zendesk_poll"

# Zendesk requires start_time to be at least 1 minute in the past.
_INITIAL_LOOKBACK_SECONDS = 90

_sfn = None


def _sfn_client():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    return _sfn


async def fetch_requester_email(client: httpx.AsyncClient, requester_id: int) -> str | None:
    resp = await zendesk_tokens.authed_request(client, "GET", f"{za.BASE_URL}/users/{requester_id}.json")
    return resp.json()["user"]["email"]


def start_pipeline(ticket_id: str) -> None:
    _sfn_client().start_execution(
        stateMachineArn=os.environ["MAIN_PIPELINE_STATE_MACHINE_ARN"],
        name=ticket_id,
        input='{"ticket_id": "%s"}' % ticket_id,
    )


async def process_ticket(raw_ticket: dict, client: httpx.AsyncClient) -> bool:
    """Persists the raw ticket and starts its pipeline execution. Returns True
    if the ticket was ingested and dispatched."""
    zendesk_ticket_id = str(raw_ticket["id"])
    try:
        email = await fetch_requester_email(client, raw_ticket["requester_id"])
        ticket_text = raw_ticket.get("description") or raw_ticket.get("subject") or ""

        ticket_id = dynamo.create_ticket(zendesk_ticket_id, email, ticket_text)
        # create_ticket does not write this record itself.
        dynamo.append_audit_record(
            ticket_id,
            "ticket_ingested",
            "ingestion",
            {"zendesk_ticket_id": zendesk_ticket_id, "customer_email": email},
        )
        logger.info("Ingested ticket %s (customer=%s)", zendesk_ticket_id, email)
    except Exception:
        logger.exception("Failed to ingest ticket %s", zendesk_ticket_id)
        return False

    try:
        start_pipeline(ticket_id)
    except Exception:
        logger.exception("Pipeline start failed for ticket %s", ticket_id)
        dynamo.append_audit_record(ticket_id, "pipeline_start_failed", "ingestion", {})
        return False
    return True


async def poll_once(client: httpx.AsyncClient, seen: set[str] | None = None) -> int:
    """Drains every page currently available (loops until end_of_stream), so a
    backlog doesn't wait multiple poll intervals to clear. Returns the number
    of new tickets dispatched."""
    seen = seen if seen is not None else set()
    cursor = state_store.get_cursor()
    start_time = int(time.time()) - _INITIAL_LOOKBACK_SECONDS

    dispatched = 0
    while True:
        params = {"cursor": cursor} if cursor else {"start_time": start_time}
        resp = await zendesk_tokens.authed_request(
            client, "GET", f"{za.BASE_URL}/incremental/tickets/cursor", params=params
        )
        data = resp.json()

        for raw_ticket in data.get("tickets", []):
            # Zendesk's incremental export includes deleted tickets in the
            # stream (a deletion counts as an update), never treat one as new.
            if raw_ticket.get("status") == "deleted":
                continue
            zendesk_ticket_id = str(raw_ticket["id"])
            if zendesk_ticket_id in seen or dynamo.ticket_exists_for_zendesk_id(zendesk_ticket_id):
                continue
            seen.add(zendesk_ticket_id)
            if await process_ticket(raw_ticket, client):
                dispatched += 1

        cursor = data.get("after_cursor") or cursor
        if cursor:
            state_store.save_cursor(cursor)

        if data.get("end_of_stream", True):
            break

    return dispatched


async def _run(context) -> dict:
    owner = str(uuid.uuid4())
    if not state_store.acquire_lease(POLL_LEASE_NAME, owner, LEASE_SECONDS):
        logger.info("Previous poller invocation still running, skipping")
        return {"skipped": "overlap"}

    started = time.monotonic()
    total = 0
    polls = 0
    seen: set[str] = set()
    try:
        if not zendesk_tokens.is_authorized():
            logger.warning("Zendesk not authorized yet, visit /oauth/authorize once to grant access")
            return {"skipped": "not_authorized"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(POLLS_PER_INVOCATION):
                try:
                    total += await poll_once(client, seen)
                except Exception:
                    logger.exception("Zendesk poll cycle failed")
                polls += 1
                if i == POLLS_PER_INVOCATION - 1:
                    break
                wait = started + (i + 1) * POLL_INTERVAL_SECONDS - time.monotonic()
                remaining = context.get_remaining_time_in_millis() / 1000 if context else float("inf")
                # Not enough time left for the wait plus another poll.
                if wait + 10 > remaining:
                    break
                if wait > 0:
                    await asyncio.sleep(wait)
    finally:
        state_store.release_lease(POLL_LEASE_NAME, owner)

    if total:
        logger.info("Dispatched %d new ticket(s)", total)
    return {"dispatched": total, "polls": polls}


def lambda_handler(event, context):
    if not za.zendesk_configured():
        logger.warning(
            "ZENDESK_SUBDOMAIN / ZENDESK_OAUTH_CLIENT_ID / ZENDESK_OAUTH_CLIENT_SECRET not set, poller is not running"
        )
        return {"skipped": "not_configured"}
    return asyncio.run(_run(context))
