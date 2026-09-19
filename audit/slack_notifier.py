"""Slack Notifier: posts a human-readable reasoning trail (not a JSON dump)
for every outcome: blocked, escalated, or executed.

Posts via the Slack Web API (chat.postMessage, bot token) rather than an
incoming webhook, since a webhook never returns a message ts to thread under.
The first post for a ticket opens the thread; every later post replies into it.
The thread ts is stored on the ticket with a conditional write, and also
recorded in a thread to ticket lookup item so the events Lambda can map a
reply back to its ticket (tickets have no index on the thread ts).
"""

import logging
import os

import httpx

from ingestion import state_store
from persistence import dynamo

logger = logging.getLogger("audit.slack_notifier")

SLACK_API_BASE = "https://slack.com/api"

# Slack section blocks cap text at 3000 chars.
_MAX_BLOCK_CHARS = 2800


def _load_ticket_summary(ticket_id: str) -> dict:
    ticket = dynamo.get_ticket(ticket_id)
    if ticket is None:
        return {"zendesk_ticket_id": "?", "customer_email": "?"}
    return {"zendesk_ticket_id": ticket["zendesk_ticket_id"], "customer_email": ticket["customer_email"]}


def _load_thread(ticket_id: str) -> str | None:
    ticket = dynamo.get_ticket(ticket_id)
    return ticket["slack_thread_ts"] if ticket else None


def _save_thread(ticket_id: str, channel: str, thread_ts: str) -> None:
    if dynamo.set_ticket_slack_thread(ticket_id, channel, thread_ts):
        try:
            state_store.put_thread_mapping(thread_ts, ticket_id)
        except Exception:
            # The events Lambda falls back to a scan for an unmapped thread.
            logger.exception("Could not record Slack thread lookup for ticket %s", ticket_id)


async def _post(ticket_id: str, blocks: list[dict], fallback_text: str) -> None:
    """Posts to SLACK_CHANNEL_ID, threading under the ticket's existing message."""
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    channel_id = os.getenv("SLACK_CHANNEL_ID")
    if not bot_token or not channel_id:
        logger.warning("SLACK_BOT_TOKEN / SLACK_CHANNEL_ID not set, skipping Slack post: %s", fallback_text)
        return

    thread_ts = _load_thread(ticket_id)
    payload = {"channel": channel_id, "text": fallback_text, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers={"Authorization": f"Bearer {bot_token}"},
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


async def notify_blocked(ticket_id: str, matched_rule: str | None, reason: str | None) -> None:
    summary = _load_ticket_summary(ticket_id)
    blocks = [
        _header("\U0001F6AB", "Blocked by policy"),
        _fields(summary, {"Matched rule": matched_rule or "unknown"}),
        _text_block("Reason", reason or "no reason provided"),
    ]
    await _post(ticket_id, blocks, f"Blocked ticket #{summary['zendesk_ticket_id']}: {matched_rule}")


async def notify_escalated(
    ticket_id: str,
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
    ticket_id: str,
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


async def notify_execution_failed(ticket_id: str, error_detail: str) -> None:
    summary = _load_ticket_summary(ticket_id)
    blocks = [
        _header("\U0001F534", "Execution failed"),
        _fields(summary, {}),
        _text_block("Stripe error", error_detail),
    ]
    await _post(ticket_id, blocks, f"Execution failed for ticket #{summary['zendesk_ticket_id']}")


async def notify_command_blocked(
    ticket_id: str, command_text: str, matched_rule: str | None, reason: str | None
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


async def notify_command_needs_clarification(ticket_id: str, command_text: str, rationale: str) -> None:
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
