"""Terminal Lambda for every branch of both state machines (main pipeline
and command pipeline). Deliberately minimal for now: records the pipeline's
outcome and finalizes ticket status where an upstream state has not already
done so. A later phase replaces the body with real Slack and Zendesk
notifications (the audit/slack_notifier.py and audit/zendesk_updater.py
equivalents) without needing to change how either state machine invokes
this handler, the event contract below stays fixed.

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
      written by verifier_agent/verify_handler.py itself, a later phase
      pulls that via persistence.dynamo.get_latest_verification_result
      when it needs the full text for a Slack message.
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

import logging

from persistence import dynamo

logger = logging.getLogger("audit.audit_handler")

_SETS_ESCALATED = {"verifier_agreed_escalate", "verifier_mismatch"}


def lambda_handler(event: dict, context) -> dict:
    ticket_id = event["ticket_id"]
    outcome = event["outcome"]

    if outcome in _SETS_ESCALATED:
        dynamo.update_ticket_status(ticket_id, "escalated")

    detail = {k: v for k, v in event.items() if k not in ("ticket_id", "outcome")}
    dynamo.append_audit_record(ticket_id, "pipeline_complete", "audit", {"outcome": outcome, **detail})

    logger.info("Ticket %s pipeline complete, outcome=%s", ticket_id, outcome)
    return {"ticket_id": ticket_id, "outcome": outcome, "handled": True}
