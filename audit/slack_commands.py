"""Slack Events API endpoint for the `@backstop <command>` feature: a human
replies in-thread under a ticket's Slack audit post, mentioning the bot, and
the command pipeline state machine turns that free text into an executed
action.

Requires a Slack app with bot scopes chat:write, channels:history (or
groups:history for a private channel), app_mentions:read, and Event
Subscriptions pointed at POST /slack/events on the API Gateway URL.

Slack needs a 200 within 3 seconds, so this only verifies, de-duplicates and
starts the state machine execution asynchronously (StartExecution returns
without waiting for the run). The event to ticket mapping is unchanged: the
reply's thread_ts is the ts of the ticket's first audit post, which the
notifier recorded when it opened the thread.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import time

import boto3

from ingestion import state_store
from ingestion.http_util import header, json_response, raw_body

logger = logging.getLogger("audit.slack_commands")

# Slack retries an event if it doesn't get a 200 within 3s, and replays events
# up to 5 minutes old, so reject anything older outright.
_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5

# Slack can deliver the same event more than once, retries arrive within
# minutes, a day is well beyond that.
_EVENT_DEDUP_TTL_SECONDS = 24 * 60 * 60

_sfn = None


def _sfn_client():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    return _sfn


def _allowed_user_ids() -> set[str]:
    # Allowlist, not "anyone in the channel", so a compromised integration can't
    # trigger a real Stripe action.
    raw = os.getenv("SLACK_COMMAND_ALLOWED_USER_IDS", "")
    return {u.strip() for u in raw.split(",") if u.strip()}


def _verify_slack_signature(timestamp: str, signature: str, body: bytes) -> bool:
    signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    if not signing_secret:
        return False
    try:
        skew = abs(time.time() - float(timestamp))
    except ValueError:
        return False
    if skew > _MAX_TIMESTAMP_SKEW_SECONDS:
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + body
    computed = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def _extract_command(text: str) -> str | None:
    """Returns the command text after an @backstop mention, or None if the
    message doesn't mention the bot at all."""
    stripped = text.strip()
    bot_user_id = os.getenv("SLACK_BOT_USER_ID")
    if bot_user_id and f"<@{bot_user_id}>" in stripped:
        return stripped.split(f"<@{bot_user_id}>", 1)[1].strip()
    lowered = stripped.lower()
    if "@backstop" in lowered:
        idx = lowered.index("@backstop")
        return stripped[idx + len("@backstop") :].strip()
    return None


def _execution_name(event_id: str | None) -> str | None:
    """Step Functions execution names are limited to 80 chars of a safe set,
    naming by Slack event id makes a redelivered event a no-op."""
    if not event_id:
        return None
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"cmd-{event_id}")[:80]


def lambda_handler(event, context):
    body = raw_body(event)
    timestamp = header(event, "X-Slack-Request-Timestamp")
    signature = header(event, "X-Slack-Signature")
    if not _verify_slack_signature(timestamp, signature, body):
        return json_response(401, {"detail": "Invalid Slack request signature"})

    try:
        payload = json.loads(body)
    except ValueError:
        return json_response(400, {"detail": "Body is not valid JSON"})

    if payload.get("type") == "url_verification":
        return json_response(200, {"challenge": payload["challenge"]})

    if payload.get("type") != "event_callback":
        return json_response(200, {"ok": True})

    event_id = payload.get("event_id")
    dedup_key = f"slack_event:{event_id}" if event_id else None
    if dedup_key:
        if not state_store.claim_key(dedup_key, _EVENT_DEDUP_TTL_SECONDS):
            logger.info("Ignoring duplicate Slack event_id=%s", event_id)
            return json_response(200, {"ok": True})

    slack_event = payload.get("event", {})
    logger.info(
        "Slack event received: type=%s subtype=%s bot_id=%s",
        slack_event.get("type"),
        slack_event.get("subtype"),
        slack_event.get("bot_id"),
    )
    # Only threaded replies matter, and bot messages (including our own audit
    # posts) must be ignored to avoid ever reacting to our own output.
    if slack_event.get("type") != "message" or slack_event.get("subtype") or slack_event.get("bot_id"):
        return json_response(200, {"ok": True})
    thread_ts = slack_event.get("thread_ts")
    if not thread_ts or thread_ts == slack_event.get("ts"):
        logger.info("Ignoring non-threaded message (thread_ts=%s, ts=%s)", thread_ts, slack_event.get("ts"))
        return json_response(200, {"ok": True})

    text = slack_event.get("text", "")
    command_text = _extract_command(text)
    if command_text is None:
        logger.info("No @backstop mention found in message text=%r", text)
        return json_response(200, {"ok": True})
    if not command_text:
        logger.info("Ignoring empty @backstop command in thread %s", thread_ts)
        return json_response(200, {"ok": True})

    slack_user_id = slack_event.get("user")
    if not slack_user_id or slack_user_id not in _allowed_user_ids():
        logger.warning(
            "Ignoring @backstop command from unauthorized Slack user %s in thread %s", slack_user_id, thread_ts
        )
        return json_response(200, {"ok": True})

    ticket_id = state_store.get_ticket_id_for_thread(thread_ts)
    if ticket_id is None:
        logger.warning("Could not resolve a ticket for Slack thread_ts=%s, ignoring command", thread_ts)
        return json_response(200, {"ok": True})

    logger.info("Dispatching @backstop command for ticket %s from user %s: %r", ticket_id, slack_user_id, command_text)
    kwargs = {
        "stateMachineArn": os.environ["COMMAND_PIPELINE_STATE_MACHINE_ARN"],
        "input": json.dumps(
            {
                "ticket_id": ticket_id,
                "raw_command": command_text,
                "slack_user_id": slack_user_id,
                "slack_channel": slack_event.get("channel", ""),
            }
        ),
    }
    name = _execution_name(event_id)
    if name:
        kwargs["name"] = name
    try:
        _sfn_client().start_execution(**kwargs)
    except Exception as e:
        if getattr(e, "response", {}).get("Error", {}).get("Code") == "ExecutionAlreadyExists":
            return json_response(200, {"ok": True})
        logger.exception("Could not start command pipeline for ticket %s", ticket_id)
        # Let Slack's retry deliver the event again.
        if dedup_key:
            state_store.release_key(dedup_key)
        return json_response(500, {"detail": "Could not start command pipeline"})
    return json_response(200, {"ok": True})
