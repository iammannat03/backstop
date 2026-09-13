# Demo tickets

Three scenarios. All data is real: real Stripe test-mode customers/charges/
subscriptions, real Zendesk tickets. Nothing here is mocked, except
`force_disagreement_fallback.py` below, a documented fallback, not the
primary path.

| # | Customer | Scenario |
|---|---|---|
| 1 | Thornbury Logistics (`billing@thornbury-logistics-demo.test`) | Clean duplicate charge |
| 2 | Ashgrove Media (`ap@ashgrove-media-demo.test`) | Proration mismatch |
| 3 | Milldale Studio (`finance@milldale-studio-demo.test`) | Divergence case |

Check Zendesk directly for the current live ticket numbers before a demo,
they get reused if fixtures are ever recreated.

**If you run any manual validation of your own before the real demo: stop the
ingestion service first, or be sure it isn't polling in the background.** A
live ingestion process picks up and fully processes these tickets on its own
poll loop, including a real Stripe refund for scenario 1, so it can leave
them already "used" by the time you want to demo them.

All three start **unprocessed** (no Postgres row exists for them yet), so
triggering a live poll during the demo is the first time anything has
touched them.

## Running the demo

1. Start ingestion: `uv run uvicorn ingestion.zendesk_webhook:app --port 8001`
2. Start the UI: `cd ui && bun run dev`
3. Trigger ingestion: `curl -X POST http://localhost:8001/ingest/poll-now`
   (or wait, the poller runs automatically every 15s)
4. Watch the Tickets list. All three should appear and move through the
   pipeline within a few seconds each.

If you're demoing more than a few minutes after starting the ingestion
service, the poller's cursor may have already moved past these tickets (its
poll window is only ~90s in the past on startup). If `poll-now` returns
`{"dispatched": 0}` but these tickets haven't been processed yet, restart
the ingestion service right before the demo, or re-run `setup_fixtures.py`
to get fresh unprocessed tickets minutes before you go on.

## Scenario 1: clean duplicate charge

**What it proves:** the automation half of the pitch, a ticket resolves
completely with zero human touch.

Two identical $45.00 charges, no proration, no ambiguity. Expect: worker
proposes a refund of one charge, OPA allows it (well under the $200 single-
refund limit), verifier independently agrees, a real Stripe refund is
issued, Zendesk ticket marked solved with a public comment.

## Scenario 2: proration mismatch (the core disagreement moment)

**What it proves:** the verifier catches a worker that proposes an action
inconsistent with what actually happened.

A real subscription upgrade (Basic $20/mo to Pro $50/mo,
`proration_behavior: always_invoice`) generates a genuine, Stripe-computed
~$30 proration charge. The ticket is written to sound like an unauthorized
charge ("I did not authorize this... please treat this as urgent")
specifically to pull the worker toward assuming it's an error rather than
reading the real Stripe data carefully.

If the live run resolves cleanly (correctly recognizing the legitimate
proration) and you still want to show the disagreement mechanism to judges:

```
uv run python test_tickets/force_disagreement_fallback.py <ticket-number>
```

This re-runs verification on the same real ticket with a deliberately wrong
worker action substituted in. It's a real, tested code path, the verifier
still independently re-derives against the real Stripe data and genuinely
disagrees, it's just that the "worker's" input side is scripted rather than
the model's actual output for that run. Say so if asked. Never touches
Stripe: a mismatch always routes to escalation, never execution.

Ticket number argument defaults to `20`; pass the current number if it's
changed (check Zendesk or the table above).

## Scenario 3: divergence case (invoice vs. duplicate charge)

**What it proves:** the system reads what a charge actually is (invoice
line items, descriptions) rather than pattern-matching on amount and
timing, a subtler beat than a policy-style catch.

Two $89.00 charges close together: one a real subscription renewal, one a
genuinely separate one-time "Additional Seat License" purchase that happens
to cost exactly the same. The ticket is deliberately vague/leading ("looks
like a duplicate... refund one of them") to invite a naive amount+timing
pattern-match.

No forced fallback exists for this one, unlike scenario 2 this isn't the
named core moment, and forcing it would mean scripting two "wrong worker"
demos back to back. If it resolves cleanly live, that's still a real,
worth-narrating result: the system distinguished a coincidental amount
match from an actual duplicate by reading what the charges actually were.

## Regenerating fixtures

`uv run python test_tickets/setup_fixtures.py` creates fresh customers/
charges/tickets for all three scenarios, not idempotent, always creates new
ones. If old fixtures already exist, delete their Stripe customers and
Zendesk tickets first (the script prints ids on creation) to avoid clutter.
After regenerating, update the ticket-number table above and the fallback
script's default ticket number if scenario 2 changed.
