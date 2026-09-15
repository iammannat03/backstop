---

## title: "Backstop: 2-Minute Hackathon Demo"
duration: "2:00 max"
created: "2026-09-14"
status: "draft"

# Backstop Demo Script

**Duration**: 2:00 max (hard cap. Judges are scoring on demo clarity too, 10% of rubric)
**Style**: Technical product demo, recorded (not live)
**Target Audience**: Phillip Li & Akira Tong (Arga Labs: agent reliability / fraud lens), Ankur Dahama & Hai Ta (Userlens: human-visibility lens)

**One thing to hold onto while recording:** every section below exists to set up one moment: the worker correctly diagnosing a charge as legit proration, then proposing a refund anyway, and the verifier catching that mismatch by re-deriving from scratch, not by critiquing the worker's words. That's the wow factor. The `@backstop` command section is the second-most important beat: it's the human-override half of the same trust story, and it was missing from the first draft of this script.

---

## Demo customers (copy-paste)

Stripe test-mode customers are already created. Do **not** re-create them. Submit tickets through the Loopline widget at `http://localhost:3002` using the exact name/email/message below. Ingestion must be running for a submit to land.

| When                          | Customer      | Email                                  | Stripe               | Billing already on the account                                                        |
| ----------------------------- | ------------- | -------------------------------------- | -------------------- | ------------------------------------------------------------------------------------- |
| Live, on camera               | Jordan Rivera | `jordan.rivera@loopline-demo.test`     | `cus_VFrU3cJEsjMU2K` | Two $89.00 "Loopline Pro (monthly)" charges                                           |
| Pre-process, wow moment       | Maya Hale     | `maya.hale@hale-audio-demo.test`       | `cus_VFrUNNTQtiI7YY` | Basic $20 -> Pro $50 upgrade; extra **$30.00** "Subscription update" proration        |
| Pre-process, then `@backstop` | Chris Owens   | `chris.owens@osprey-digital-demo.test` | `cus_VFrU3nOjRjtBDx` | Active $40/mo Osprey Pro (`sub_1UFLlWCB1n4cRH2TMPfRjZx9`) + $25.00 "Reactivation fee" |

Emails are unique so they will not collide with the older Thornbury / Ashgrove / Milldale fixtures.

---

## 0:00-0:12: HOOK

### VISUAL

Screen recording starts on the Backstop dashboard, a ticket mid-flight. Freeze/zoom on the decision trail panel.

### VOICEOVER

"This is Backstop: an AI agent that reads support tickets and takes real action on Stripe: refunds, cancellations, fraud flags. But what happens when it's confidently wrong? Backstop is built so it's allowed to be wrong, as long as it never acts on it."

### NOTES

First sentence is the "what is this" line judges need before anything else lands. Second and third sentences are the hook. Slightly faster pace is fine here.

---

## 0:12-0:38: ARCHITECTURE (Excalidraw)

### VISUAL

Cut to Excalidraw. Draw live, left to right, as you narrate:
`Zendesk ticket → Worker Agent → OPA Policy Gate → Verifier Agent → Stripe`
Add the Worker → Verifier callout: "NO. Verifier never sees this."
Then draw a second, shorter branch feeding into the same OPA gate box: `Human (@backstop in Slack) → OPA Policy Gate → Stripe`, with a small note "verifier skipped: human already decided."

### VOICEOVER

"The ticket goes to a worker agent, which investigates Stripe and proposes an action. Before anything executes, two independent checks run: a deterministic policy gate, and a second agent that never sees the worker's output. It re-derives its own answer from raw data. Only if both agree does Backstop touch Stripe. A human can also just type a plain-English command in Slack: that skips the verifier, since there's nothing left to independently check once a person has made the call, but it still has to clear the exact same policy gate."

### NOTES

This is the Arga Labs moment. Independent verification and policy gate are their language. The second branch matters too: it shows the same trust boundary applies whether the actor is an agent or a human, not just a special case bolted on.

---

## 0:38-0:55: LOOPLINE + LAYER WALKTHROUGH

### TYPE THIS INTO THE LOOPLINE WIDGET (live, on camera)

This is also the **clean auto-resolve** case. $89 matches the "Next invoice $89.00" on the Loopline dashboard.

- **Name:** `Jordan Rivera`
- **Email:** `jordan.rivera@loopline-demo.test`
- **Message:** `I was charged twice for this month's plan, can you refund the duplicate?`

Expect: worker refunds one $89 charge, OPA allows it, verifier agrees, Stripe refund executes.

### VISUAL

Switch to Loopline (the dummy product with the embedded support widget). Submit the request above live through the widget. Cut briefly back to the architecture diagram or dashboard to show the same request lighting up each stage in order.

### VOICEOVER

"This is Loopline: a sample product with Backstop embedded as its support layer, not bolted on top. A customer submits a request here... and it flows straight through: ingestion, investigation, policy check, verification, execution. Live."

### NOTES

Proves "embeddable infrastructure". Matters for the Userlens judges too, since it reads as a layer a real product sits behind, not a toy.

---

## 0:55-1:25: TEST CASES + THE WOW MOMENT

### PRE-PROCESS THIS ONE BEFORE RECORDING (not live)

Submit through Loopline, then wait for the pipeline. If it resolves cleanly (worker correctly says no refund), force the disagreement with:

```
uv run python test_tickets/force_disagreement_fallback.py <zendesk-ticket-number>
```

That is the beat the voiceover describes: worker rationale says legitimate proration, proposed action is still a refund, verifier independently disagrees.

- **Name:** `Maya Hale`
- **Email:** `maya.hale@hale-audio-demo.test`
- **Message:** `I just noticed an extra $30 charge on our account that I did not authorize. This needs to be refunded immediately, please treat this as urgent.`

Stripe behind it: Basic $20/mo upgraded to Pro $50/mo (`proration_behavior: always_invoice`). Real extra charge is **$30.00** (`ch_3UFLlSCB1n4cRH2T1ZGeZDOx`, description "Subscription update"), plus the original $20 subscription-creation charge.

### VISUAL

Montage: 2 tickets processed, one live one sped-through ("processing in background, here's the result"). Land hard on the proration-mismatch case: sequential zoom on worker's reasoning ("this is legitimate proration") next to worker's proposed action ("refund") next to verifier's independent conclusion ("disagree: no refund warranted"), then the escalated result.

Live/sped-through ticket = **Jordan Rivera** (the Loopline submit from the previous section).
Wow ticket = **Maya Hale**.

### VOICEOVER

"Here's a clean case, resolved automatically. And here's the one that matters: the worker correctly identifies this charge as legitimate proration... but still proposes a refund. The verifier doesn't read the worker's explanation. It re-derives the answer from scratch, lands somewhere different, and the mismatch stops execution cold."

### NOTES

Protect this in editing. Don't rush it, don't cut away from it. Everything else can compress; this can't.

---

## 1:25-1:40: THE `@backstop` COMMAND (human override)

### PRE-PROCESS THIS ONE BEFORE RECORDING

Submit through Loopline so a Slack thread exists. This customer has **no** Stripe `fraud_flag`, so a human command can still clear the policy gate. The ticket wording is meant to land as a hold / fraud-review / escalate, not an auto-refund.

- **Name:** `Chris Owens`
- **Email:** `chris.owens@osprey-digital-demo.test`
- **Message:** `I think someone has access to my account. I didn't authorize this subscription or the $25 reactivation fee. Please look into this as fraud.`

### TYPE THIS IN SLACK (live, on camera)

In the Slack thread for Chris Owens' escalated ticket:

```
@backstop cancel their subscription, waive the fee
```

Stripe behind it: active $40/mo Osprey Pro (`sub_1UFLlWCB1n4cRH2TMPfRjZx9`) and a $25.00 "Reactivation fee" (`ch_3UFLlYCB1n4cRH2T1VUuXeOf`). The parser emits one action (cancel _or_ credit), not both. Cancel is the visible one on camera; the $25 fee is there if it maps to a credit instead.

### VISUAL

Cut to Slack, a thread under the Chris Owens escalated ticket. Type the command live. Show the reply landing, then cut to the dashboard/Zendesk ticket flipping from escalated to resolved.

### VOICEOVER

"When a ticket does get escalated, a human doesn't need the dashboard at all. They can just reply in the Slack thread. `@backstop cancel their subscription, waive the fee.` It's parsed, checked against the same policy gate, and executed: same audit trail, same Zendesk write-back, just a human in the loop instead of the verifier."

### NOTES

This is the piece that was missing from the first script draft. Keep it short and concrete: one command, one visible before/after state change. Don't re-explain the architecture here; you already drew it.

---

## 1:40-1:53: SLACK, ZENDESK, DASHBOARD

### VISUAL

Quick cuts: Slack channel showing the Block Kit post with worker vs. verifier reasoning side by side; Zendesk ticket with the write-back comment; dashboard's plain-language decision summary and collapsible reasoning trail.

Best ticket to linger on: **Maya Hale** (worker vs. verifier disagreement is the Userlens-readable trail).

### VOICEOVER

"Every decision, automated or human, is posted to Slack with the reasoning laid out, written back to Zendesk, and logged to an append-only audit trail: visible in a dashboard as a plain-language summary anyone can read, with the full reasoning underneath if they want it."

### NOTES

This is the Userlens moment. Plain-language decision summary and visibility are close to their own thesis. Let the dashboard summary sit on screen for at least a beat.

---

## 1:53-2:00: CLOSE

### VISUAL

Back to the full architecture diagram, both branches visible (agent path and human `@backstop` path converging on the same gate).

### VOICEOVER

"Backstop doesn't try to make the agent never wrong. It makes sure being wrong, agent or human, never reaches Stripe unchecked."

### NOTES

End on the thesis line, extended to cover both paths since you just showed both. No call-to-action slide. No seconds left for it.

---

## Recording checklist (production order, not narration order)

1. **Have every window pre-opened and pre-positioned before you hit record**: Excalidraw (blank canvas, pipeline nodes roughly placed, including the second `@backstop` branch so the live draw is fast), Loopline widget, Backstop dashboard, Slack channel (Chris Owens already escalated, thread ready to reply under), Zendesk ticket view.
2. **Pre-process Maya Hale and Chris Owens first** (Loopline submit, wait for pipeline). For Maya, run `force_disagreement_fallback.py` if the live worker/verifier run does not already show "proration but refund". Leave Jordan Rivera unsubmitted until the live Loopline take.
3. **Record the Excalidraw draw separately if needed** and speed it up slightly in editing.
4. **Do the proration-mismatch case as its own clean take** (Maya Hale). It's the section judges will remember most.
5. **Do the** `@backstop` **command as its own clean take too** (Chris Owens). Type the command slowly enough to read on camera, and have the before/after ticket state ready to cut to immediately. Don't wait on real pipeline latency during the recording.
6. **Time yourself reading the voiceover out loud before recording video.** This script runs close to 2:00 with very little slack.
7. **Don't say "fully unscripted" anywhere in the narration.** That claim doesn't hold up if a judge asks about it directly.
8. **Cut, don't pause, between sections.**
