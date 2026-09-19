"""Zendesk write-back: closes the loop on the original customer-facing ticket
once a decision is final. Solved + public comment when executed. Open +
internal note when escalated ("pending" would mean waiting on the customer,
which is wrong here since it's an agent who needs to act next).

Requests go through ingestion.zendesk_tokens, which shares one OAuth token
state (stored in DynamoDB) across every Lambda, to avoid a refresh-token race
with the ingestion poller.
"""

import logging

import httpx

from ingestion.zendesk_tokens import authed_request
from shared.zendesk_auth import BASE_URL

logger = logging.getLogger("audit.zendesk_updater")


async def mark_resolved(zendesk_ticket_id: str, public_comment: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        await authed_request(
            client,
            "PUT",
            f"{BASE_URL}/tickets/{zendesk_ticket_id}.json",
            json={"ticket": {"status": "solved", "comment": {"body": public_comment, "public": True}}},
        )
    logger.info("Zendesk ticket %s marked solved", zendesk_ticket_id)


async def mark_escalated(zendesk_ticket_id: str, internal_note: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        await authed_request(
            client,
            "PUT",
            f"{BASE_URL}/tickets/{zendesk_ticket_id}.json",
            json={"ticket": {"status": "open", "comment": {"body": internal_note, "public": False}}},
        )
    logger.info("Zendesk ticket %s marked open with internal note", zendesk_ticket_id)
