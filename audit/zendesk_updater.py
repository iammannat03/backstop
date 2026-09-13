"""Zendesk write-back: closes the loop on the original customer-facing ticket
once a decision is final. Solved + public comment when executed. Open +
internal note when escalated ("pending" would mean waiting on the customer,
which is wrong here since it's an agent who needs to act next).

Uses the shared token_manager from shared/zendesk_auth.py, not its own token
state, to avoid a refresh-token race with the ingestion poller.
"""

import logging

import httpx

from shared.zendesk_auth import BASE_URL, authed_request

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
