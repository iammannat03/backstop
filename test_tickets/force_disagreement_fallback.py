"""Fallback for the proration-mismatch demo (scenario 2 in README.md).

The real worker reasoning engine may correctly identify the proration charge
as legitimate every time, which is good for the system but means a live demo
isn't guaranteed to reproduce a worker/verifier disagreement on the first
try. This script forces one, for real, against the real scenario 2 data: it
substitutes a deliberately wrong worker action and re-runs verification, so
the ticket detail page shows a genuine, live mismatch.

This is a documented fallback, not the primary path, try the real live poll
first (POST /ingest/poll-now against the Ashgrove Media ticket). Only reach
for this if that run resolves cleanly and you still want to show the
disagreement mechanism live.

Safe to run: a mismatch routes to "escalated", never to execution, this
never calls Stripe.

Usage: uv run python test_tickets/force_disagreement_fallback.py <zendesk_ticket_id>
(check test_tickets/README.md for the current scenario 2 ticket number.)
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persistence.db import SessionLocal  # noqa: E402
from persistence.models import Ticket  # noqa: E402
from shared.models import ProposedAction  # noqa: E402
from verifier_agent.pipeline import run_verification_pipeline  # noqa: E402


def _load_ticket_by_zendesk_id(zendesk_ticket_id: str) -> tuple[str, str]:
    """Returns (internal ticket_id as str, target_transaction_id to refund)."""
    with SessionLocal() as db:
        ticket = db.query(Ticket).filter(Ticket.zendesk_ticket_id == zendesk_ticket_id).first()
        if ticket is None:
            raise SystemExit(
                f"No ticket found for zendesk_ticket_id={zendesk_ticket_id}. "
                "Has it been ingested yet? Trigger POST /ingest/poll-now first."
            )
        proposed = ticket.proposed_action or {}
        # Whatever transaction the worker actually looked at, the specific id
        # doesn't matter for the forced mismatch, the point is the action.
        target = proposed.get("target_transaction_id")
        return str(ticket.id), target


async def main() -> None:
    zendesk_ticket_id = sys.argv[1] if len(sys.argv) > 1 else "20"
    ticket_id_str, target = _load_ticket_by_zendesk_id(zendesk_ticket_id)

    wrong_action = ProposedAction(
        action_type="refund",
        amount=3000,
        currency="usd",
        target_transaction_id=target,
        rationale=(
            "This charge is a legitimate proration adjustment for the plan upgrade, "
            "but issuing the refund per the customer's request."
        ),
        confidence=0.8,
    )
    await run_verification_pipeline(uuid.UUID(ticket_id_str), wrong_action)
    print(f"Forced a disagreement on ticket zendesk#{zendesk_ticket_id}, check the ticket detail page now.")


if __name__ == "__main__":
    asyncio.run(main())
