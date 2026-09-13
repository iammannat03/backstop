"""Slack Notifier: posts a human-readable reasoning trail (not a JSON dump)
for every outcome: blocked, escalated, or executed.

Posts via the Slack Web API (chat.postMessage, bot token) rather than an
incoming webhook, since a webhook never returns a message ts to thread under.
The first post for a ticket opens the thread; every later post replies into it.
"""

import logging
import os
import uuid

import httpx
from dotenv import load_dotenv

from persistence.db import SessionLocal
from persistence.models import Ticket

load_dotenv()

logger = logging.getLogger("audit.slack_notifier")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")
SLACK_API_BASE = "https://slack.com/api"

# Slack section blocks cap text at 3000 chars.
_MAX_BLOCK_CHARS = 2800


def _load_ticket_summary(ticket_id: uuid.UUID) -> dict:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return {"zendesk_ticket_id": "?", "customer_email": "?"}
        return {"zendesk_ticket_id": ticket.zendesk_ticket_id, "customer_email": ticket.customer_email}


def _load_thread(ticket_id: uuid.UUID) -> str | None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        return ticket.slack_thread_ts if ticket else None


def _save_thread(ticket_id: uuid.UUID, channel: str, thread_ts: str) -> None:
    with SessionLocal() as db:
        ticket = db.get(Ticket, ticket_id)
        if ticket is not None and not ticket.slack_thread_ts:
            ticket.slack_channel = channel
            ticket.slack_thread_ts = thread_ts
            db.commit()


async def _post(ticket_id: uuid.UUID, blocks: list[dict], fallback_text: str) -> None:
    """Posts to SLACK_CHANNEL_ID, threading under the ticket's existing message."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        logger.warning(
            "SLACK_BOT_TOKEN / SLACK_CHANNEL_ID not set, skipping Slack post: %s", fallback_text
        )
        return

    thread_ts = _load_thread(ticket_id)
    payload = {"channel": SLACK_CHANNEL_ID, "text": fallback_text, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            # Slack's Web API returns HTTP 200 even on failure.
            raise RuntimeError(f"Slack chat.postMessage failed: {data.get('error')}")

    if not thread_ts:
        _save_thread(ticket_id, data["channel"], data["ts"])


def _header(emoji: str, title: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}"}}


def _fields(summary: dict, extra: dict) -> dict:
    lines = [f"*Ticket:* #{summary['zendesk_ticket_id']}", f"*Customer:* {summary['customer_email']}"]
    lines.extend(f"*{key}:* {value}" for key, value in extra.items())
    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}


def _text_block(label: str, text: str) -> dict:
    trimmed = text if len(text) <= _MAX_BLOCK_CHARS else text[:_MAX_BLOCK_CHARS] + "..."
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*{label}:*\n>{trimmed}"}}


async def notify_blocked(ticket_id: uuid.UUID, matched_rule: str | None, reason: str | None) -> None:
    summary = _load_ticket_summary(ticket_id)
    blocks = [
        _header("\U0001F6AB", "Blocked by policy"),
        _fields(summary, {"Matched rule": matched_rule or "unknown"}),
        _text_block("Reason", reason or "no reason provided"),
    ]
    await _post(ticket_id, blocks, f"Blocked ticket #{summary['zendesk_ticket_id']}: {matched_rule}")


async def notify_escalated(
    ticket_id: uuid.UUID,
    mismatch_type: str | None,
    notes: str,
    worker_rationale: str,
    verifier_rationale: str,
    header: str = "Escalated: verifier disagreement",
    field_label: str = "Mismatch",
) -> None:
    summary = _load_ticket_summary(ticket_id)
    blocks = [
        _header("⚠️", header),
        _fields(summary, {field_label: mismatch_type or "low_confidence"}),
        _text_block("Worker's reasoning", worker_rationale),
        _text_block("Verifier's independent finding", verifier_rationale),
        _text_block("Summary", notes),
    ]
    await _post(ticket_id, blocks, f"Escalated ticket #{summary['zendesk_ticket_id']}: {mismatch_type}")


async def notify_executed(
    ticket_id: uuid.UUID,
    action_type: str,
    amount: int,
    currency: str,
    rationale: str,
    refund_id: str | None,
) -> None:
    summary = _load_ticket_summary(ticket_id)
    amount_str = f"{amount / 100:.2f} {currency.upper()}" if amount else "n/a"
    blocks = [
        _header("✅", "Executed"),
        _fields(summary, {"Action": action_type, "Amount": amount_str, "Refund ID": refund_id or "n/a"}),
        _text_block("Reasoning", rationale),
    ]
    await _post(ticket_id, blocks, f"Executed ticket #{summary['zendesk_ticket_id']}: {action_type} {amount_str}")


async def notify_execution_failed(ticket_id: uuid.UUID, error_detail: str) -> None:
    summary = _load_ticket_summary(ticket_id)
    blocks = [
        _header("\U0001F534", "Execution failed"),
        _fields(summary, {}),
        _text_block("Stripe error", error_detail),
    ]
    await _post(ticket_id, blocks, f"Execution failed for ticket #{summary['zendesk_ticket_id']}")


async def notify_command_blocked(
    ticket_id: uuid.UUID, command_text: str, matched_rule: str | None, reason: str | None
) -> None:
    """Reply for a human's `@backstop <command>` that OPA denied."""
    summary = _load_ticket_summary(ticket_id)
    blocks = [
        _header("\U0001F6AB", "Command blocked by policy"),
        _fields(summary, {"Command": command_text, "Matched rule": matched_rule or "unknown"}),
        _text_block("Reason", reason or "no reason provided"),
    ]
    await _post(
        ticket_id, blocks, f"Command blocked on ticket #{summary['zendesk_ticket_id']}: {matched_rule}"
    )


async def notify_command_needs_clarification(ticket_id: uuid.UUID, command_text: str, rationale: str) -> None:
    """The command parser couldn't ground the human's instruction in a
    concrete, executable action."""
    summary = _load_ticket_summary(ticket_id)
    blocks = [
        _header("❓", "Command needs clarification"),
        _fields(summary, {"Command": command_text}),
        _text_block("Why", rationale),
    ]
    await _post(
        ticket_id, blocks, f"Command needs clarification on ticket #{summary['zendesk_ticket_id']}"
    )
