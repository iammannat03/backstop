"""Terminal Lambda for every branch of both state machines (main pipeline
and command pipeline). Records the pipeline's outcome, finalizes ticket
status where an upstream state has not already done so, then sends the Slack
and Zendesk notifications for that outcome (audit/slack_notifier.py and
audit/zendesk_updater.py), mirroring what backstop-prac did at the matching
point of its pipeline. The event contract below is fixed.

Notifications never affect the pipeline: any failure is logged and swallowed,
and ticket status is settled before any notification is attempted. Each
notification is claimed with a DynamoDB key first, so a Step Functions retry
of this state does not post to Slack or write to Zendesk twice. A failed
notification releases its key, so a redelivery can try again.

Input event: {"ticket_id": str, "outcome": str, ...extra context fields}

outcome is one of:
  "policy_blocked": OPA denied or escalated the automated pipeline's
      proposed action. Extra fields: matched_rule, reason. Ticket status
      is already "escalated" (governance/policy_handler.py sets it), this
      call is a no-op on status, just records the outcome.
  "verifier_agreed_escalate": worker and verifier independently agreed on
      escalate or flag_for_fraud_review, never auto-executed even though
      "consistent" per the verifier's own check. Extra fields: action_type.
      Sets status to "escalated".
  "verifier_mismatch": worker and verifier disagreed, or the verifier's
      own confidence was too low to trust. Extra fields: mismatch_type.
      Sets status to "escalated". Full detail (notes, both rationales) is
      already in the ticket's VerificationResult row and audit trail,
      written by verifier_agent/verify_handler.py itself, the Slack message
      pulls it via persistence.dynamo.get_latest_verification_result.
  "executed": execution/execute_handler.py already ran and already
      finalized status (resolved or escalated) and its own audit records.
      Extra fields: execute_result (execute_handler's own return dict).
      This call does not touch status, only appends a completion marker.
  "command_blocked": the human command path's OPA gate denied the command.
      Extra fields: matched_rule, reason. Ticket status is left untouched,
      matching command_agent/command_handler.py, which does not change
      status on this outcome either.
  "command_needs_clarification": the human command could not be grounded
      in a concrete Stripe-backed action. Extra fields: rationale. Ticket
      status is left untouched, same reasoning as command_blocked.
"""

import asyncio
import logging

from audit import slack_notifier, zendesk_updater
from execution.execute_handler import customer_facing_resolution_message
from ingestion import state_store
from persistence import dynamo
from shared.models import ProposedAction

logger = logging.getLogger("audit.audit_handler")

_SETS_ESCALATED = {"verifier_agreed_escalate", "verifier_mismatch"}

# Long enough to cover any realistic Step Functions redelivery.
_NOTIFY_KEY_TTL_SECONDS = 7 * 24 * 60 * 60


def _latest_audit_id(ticket_id: str, event_types: tuple[str, ...]) -> str:
    """Id of the newest audit record of the given types. Distinguishes one
    execution or human command from another on the same ticket."""
    for record in reversed(dynamo.get_audit_trail(ticket_id)):
        if record["event_type"] in event_types:
            return record["id"]
    return "none"


def _latest_command_text(ticket_id: str) -> str:
    for record in reversed(dynamo.get_audit_trail(ticket_id)):
        if record["event_type"] == "command_received":
            return (record["detail"] or {}).get("command_text", "")
    return ""


def _verification_text(ticket_id: str, ticket: dict | None) -> tuple[str | None, str, str, str]:
    verification = dynamo.get_latest_verification_result(ticket_id) or {}
    worker_action = (ticket or {}).get("proposed_action") or {}
    return (
        verification.get("mismatch_type"),
        verification.get("notes", ""),
        worker_action.get("rationale", ""),
        verification.get("verifier_rationale", ""),
    )


def _plan(ticket_id: str, outcome: str, event: dict, ticket: dict | None):
    """Returns (discriminator, slack_call, zendesk_call). Each call is a
    zero-argument function returning a coroutine, or None if that channel has
    nothing to do for this outcome."""
    zendesk_id = ticket["zendesk_ticket_id"] if ticket else None

    def zendesk(fn, *args):
        return (lambda: fn(zendesk_id, *args)) if zendesk_id else None

    if outcome == "policy_blocked":
        matched_rule, reason = event.get("matched_rule"), event.get("reason")
        return (
            "",
            lambda: slack_notifier.notify_blocked(ticket_id, matched_rule, reason),
            zendesk(zendesk_updater.mark_escalated, f"Blocked by policy ({matched_rule}): {reason}"),
        )

    if outcome == "verifier_agreed_escalate":
        _, notes, worker_rationale, verifier_rationale = _verification_text(ticket_id, ticket)
        return (
            "",
            lambda: slack_notifier.notify_escalated(
                ticket_id,
                f"agreed_{event.get('action_type')}",
                notes,
                worker_rationale,
                verifier_rationale,
                header="Escalated: needs human review",
                field_label="Reason",
            ),
            zendesk(zendesk_updater.mark_escalated, f"Escalated for human review: {notes}"),
        )

    if outcome == "verifier_mismatch":
        stored_mismatch, notes, worker_rationale, verifier_rationale = _verification_text(ticket_id, ticket)
        mismatch_type = event.get("mismatch_type") or stored_mismatch
        return (
            "",
            lambda: slack_notifier.notify_escalated(
                ticket_id, mismatch_type, notes, worker_rationale, verifier_rationale
            ),
            zendesk(zendesk_updater.mark_escalated, f"Verifier flagged a mismatch: {notes}"),
        )

    if outcome == "executed":
        result = event.get("execute_result") or {}
        disc = _latest_audit_id(ticket_id, ("executed", "execution_failed"))
        if result.get("status") == "escalated":
            error_detail = result.get("error") or "Execution failed"
            return (
                disc,
                lambda: slack_notifier.notify_execution_failed(ticket_id, error_detail),
                zendesk(
                    zendesk_updater.mark_escalated,
                    f"Automated execution failed and needs manual handling: {error_detail}",
                ),
            )
        # execute_handler refuses to run without a stored proposed_action, the
        # fallback only guards against a ticket item that vanished since.
        stored = (ticket or {}).get("proposed_action") or {
            "action_type": result.get("action_type", "no_action"),
            "rationale": "",
            "confidence": 1.0,
        }
        action = ProposedAction(**stored)
        return (
            disc,
            lambda: slack_notifier.notify_executed(
                ticket_id, action.action_type, action.amount, action.currency, action.rationale, result.get("refund_id")
            ),
            zendesk(zendesk_updater.mark_resolved, customer_facing_resolution_message(action)),
        )

    if outcome == "command_blocked":
        disc = _latest_audit_id(ticket_id, ("command_received",))
        command_text = _latest_command_text(ticket_id)
        return (
            disc,
            lambda: slack_notifier.notify_command_blocked(
                ticket_id, command_text, event.get("matched_rule"), event.get("reason")
            ),
            None,
        )

    if outcome == "command_needs_clarification":
        disc = _latest_audit_id(ticket_id, ("command_received",))
        command_text = _latest_command_text(ticket_id)
        return (
            disc,
            lambda: slack_notifier.notify_command_needs_clarification(
                ticket_id, command_text, event.get("rationale", "")
            ),
            None,
        )

    return "", None, None


async def _send(ticket_id: str, outcome: str, channel: str, disc: str, call) -> None:
    key = f"notify:{ticket_id}:{outcome}:{disc}:{channel}"
    if not state_store.claim_key(key, _NOTIFY_KEY_TTL_SECONDS):
        logger.info("Ticket %s: %s %s notification already sent, skipping", ticket_id, outcome, channel)
        return
    try:
        await call()
    except Exception:
        logger.exception("Ticket %s: %s notification failed for outcome=%s", ticket_id, channel, outcome)
        try:
            state_store.release_key(key)
        except Exception:
            logger.exception("Ticket %s: could not release notification key %s", ticket_id, key)


async def _notify(ticket_id: str, outcome: str, event: dict) -> None:
    ticket = dynamo.get_ticket(ticket_id)
    disc, slack_call, zendesk_call = _plan(ticket_id, outcome, event, ticket)
    # Independent of each other, one failing must not skip the other.
    if slack_call:
        await _send(ticket_id, outcome, "slack", disc, slack_call)
    if zendesk_call:
        await _send(ticket_id, outcome, "zendesk", disc, zendesk_call)


def lambda_handler(event: dict, context) -> dict:
    ticket_id = event["ticket_id"]
    outcome = event["outcome"]

    if outcome in _SETS_ESCALATED:
        dynamo.update_ticket_status(ticket_id, "escalated")

    detail = {k: v for k, v in event.items() if k not in ("ticket_id", "outcome")}
    dynamo.append_audit_record(ticket_id, "pipeline_complete", "audit", {"outcome": outcome, **detail})

    try:
        asyncio.run(_notify(ticket_id, outcome, event))
    except Exception:
        logger.exception("Ticket %s: notifications failed for outcome=%s", ticket_id, outcome)

    logger.info("Ticket %s pipeline complete, outcome=%s", ticket_id, outcome)
    return {"ticket_id": ticket_id, "outcome": outcome, "handled": True}
