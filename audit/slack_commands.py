"""Slack Events API listener for the `@backstop <command>` feature: a human
replies in-thread under a ticket's Slack audit post, mentioning the bot, and
command_agent/pipeline.py turns that free text into an executed action.

Requires a Slack app with bot scopes chat:write, channels:history (or
groups:history for a private channel), app_mentions:read, and Event
Subscriptions pointed at POST /slack/events on this service.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import time
import uuid

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from persistence.db import SessionLocal
from persistence.models import Ticket

from command_agent.pipeline import run_command_pipeline

load_dotenv()

logger = logging.getLogger("audit.slack_commands")

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_BOT_USER_ID = os.getenv("SLACK_BOT_USER_ID")
# Allowlist, not "anyone in the channel", so a compromised integration can't
# trigger a real Stripe action.
_ALLOWED_USER_IDS = {u.strip() for u in os.getenv("SLACK_COMMAND_ALLOWED_USER_IDS", "").split(",") if u.strip()}

# Slack retries an event if it doesn't get a 200 within 3s, and replays events
# up to 5 minutes old, so reject anything older outright.
_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5

# In-memory de-dupe of Slack's event_id, since Slack can deliver the same
# event more than once.
_seen_event_ids: set[str] = set()
_MAX_SEEN_IDS = 2000

router = APIRouter()


def _verify_slack_signature(timestamp: str, signature: str, body: bytes) -> bool:
    if not SLACK_SIGNING_SECRET:
        return False
    if abs(time.time() - float(timestamp)) > _MAX_TIMESTAMP_SKEW_SECONDS:
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + body
    computed = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def _extract_command(text: str) -> str | None:
    """Returns the command text after an @backstop mention, or None if the
    message doesn't mention the bot at all."""
    stripped = text.strip()
    if SLACK_BOT_USER_ID and f"<@{SLACK_BOT_USER_ID}>" in stripped:
        return stripped.split(f"<@{SLACK_BOT_USER_ID}>", 1)[1].strip()
    lowered = stripped.lower()
    if "@backstop" in lowered:
        idx = lowered.index("@backstop")
        return stripped[idx + len("@backstop") :].strip()
    return None


def _resolve_ticket_id(thread_ts: str) -> uuid.UUID | None:
    with SessionLocal() as db:
        ticket_id = db.execute(select(Ticket.id).where(Ticket.slack_thread_ts == thread_ts)).scalar_one_or_none()
        return ticket_id


@router.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_signature(timestamp, signature, body):
        raise HTTPException(status_code=401, detail="Invalid Slack request signature")

    payload = await request.json()

    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    if payload.get("type") != "event_callback":
        return {"ok": True}

    event_id = payload.get("event_id")
    if event_id:
        if event_id in _seen_event_ids:
            logger.info("Ignoring duplicate Slack event_id=%s", event_id)
            return {"ok": True}
        _seen_event_ids.add(event_id)
        if len(_seen_event_ids) > _MAX_SEEN_IDS:
            _seen_event_ids.clear()

    event = payload.get("event", {})
    logger.info("Slack event received: type=%s subtype=%s bot_id=%s", event.get("type"), event.get("subtype"), event.get("bot_id"))
    # Only threaded replies matter, and bot messages (including our own audit
    # posts) must be ignored to avoid ever reacting to our own output.
    if event.get("type") != "message" or event.get("subtype") or event.get("bot_id"):
        return {"ok": True}
    thread_ts = event.get("thread_ts")
    if not thread_ts or thread_ts == event.get("ts"):
        logger.info("Ignoring non-threaded message (thread_ts=%s, ts=%s)", thread_ts, event.get("ts"))
        return {"ok": True}

    text = event.get("text", "")
    command_text = _extract_command(text)
    if command_text is None:
        logger.info("No @backstop mention found in message text=%r", text)
        return {"ok": True}
    if not command_text:
        logger.info("Ignoring empty @backstop command in thread %s", thread_ts)
        return {"ok": True}

    slack_user_id = event.get("user")
    if not slack_user_id or slack_user_id not in _ALLOWED_USER_IDS:
        logger.warning(
            "Ignoring @backstop command from unauthorized Slack user %s in thread %s", slack_user_id, thread_ts
        )
        return {"ok": True}

    ticket_id = _resolve_ticket_id(thread_ts)
    if ticket_id is None:
        logger.warning("Could not resolve a ticket for Slack thread_ts=%s, ignoring command", thread_ts)
        return {"ok": True}

    logger.info("Dispatching @backstop command for ticket %s from user %s: %r", ticket_id, slack_user_id, command_text)
    # Fire-and-forget: Slack requires a fast 200 response, well before a full
    # Gemini+Stripe+OPA round trip could complete.
    asyncio.create_task(run_command_pipeline(ticket_id, command_text, slack_user_id))
    return {"ok": True}
