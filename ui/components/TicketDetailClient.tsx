"use client";

import { useState, type CSSProperties } from "react";
import { eventActor, eventFlagged, eventName, eventPayload, eventSummary } from "@/lib/audit";
import { describeOutcome, formatMoney, isMonetary } from "@/lib/format";
import { statusLabel } from "@/lib/pipeline";
import type { TicketDetail } from "@/lib/queries";
import type { AuditRecord, ProposedAction, Ticket } from "@/lib/types";
import { usePolling } from "@/lib/usePolling";
import { Age } from "./Age";
import { BlueprintCorners } from "./Blueprint";
import { Breadcrumb } from "./Breadcrumb";

const SIG = "#e6503b";

interface Step {
  title: string;
  wBody: string;
  wMeta: string;
  vBody: string;
  vMeta: string;
  diverge?: boolean;
}

function stripeFromTrail(trail: AuditRecord[]): { body: string; meta: string } {
  const ev = [...trail].reverse().find((e) => e.event_type === "stripe_investigated");
  const d = ev?.detail;
  if (!d) return { body: "Stripe history not yet pulled.", meta: "stripe.charges.list: pending" };
  const txs = (Array.isArray(d.transactions) ? d.transactions : []) as Array<Record<string, unknown>>;
  const customerId = typeof d.customer_id === "string" ? d.customer_id : "unresolved";
  if (txs.length === 0) {
    return { body: "No charges found for this customer.", meta: `stripe.charges.list(${customerId}) → 0 records` };
  }
  const lines = txs.slice(0, 5).map((t) => {
    const amount = typeof t.amount === "number" ? formatMoney(t.amount, String(t.currency ?? "usd")) : "?";
    const proration = t.is_proration ? " · proration" : "";
    const invoice = typeof t.invoice_id === "string" && t.invoice_id ? ` · ${t.invoice_id}` : "";
    return `${amount}${proration}${invoice}`;
  });
  return {
    body: `${txs.length} charge${txs.length === 1 ? "" : "s"}: ${lines.join("; ")}.`,
    meta: `stripe.charges.list(${customerId}) → ${txs.length} record${txs.length === 1 ? "" : "s"}`,
  };
}

function firstSentence(text: string | undefined, fallback: string): string {
  if (!text) return fallback;
  const compact = text.split(/[.!\n]/)[0]?.trim();
  return compact || fallback;
}

// Only the four steps that both agents actually independently perform. The
// policy gate and the final outcome get their own dedicated blocks instead.
function buildSteps(
  ticket: Ticket,
  workerAction: ProposedAction | null,
  verifierAction: ProposedAction | null,
  verifierRationale: string | undefined,
  trail: AuditRecord[],
  amountDiverge: boolean,
): Step[] {
  const stripe = stripeFromTrail(trail);
  const zendesk = ticket.zendesk_ticket_id;

  return [
    {
      title: "Read intake",
      wBody: ticket.ticket_text || "No ticket body recorded.",
      wMeta: `zendesk.tickets.get(${zendesk}) · raw body`,
      vBody: ticket.ticket_text || "No ticket body recorded.",
      vMeta: `zendesk.tickets.get(${zendesk}) · raw body, independently re-fetched`,
    },
    {
      title: "Pull transactions",
      wBody: stripe.body,
      wMeta: stripe.meta,
      vBody: stripe.body,
      vMeta: `${stripe.meta} · independent re-fetch`,
    },
    {
      title: "Interpret evidence",
      wBody: workerAction?.rationale ?? "No rationale recorded.",
      wMeta: `confidence ${workerAction ? workerAction.confidence.toFixed(2) : "N/A"}`,
      vBody: verifierAction?.rationale ?? verifierRationale ?? "No independent rationale recorded.",
      vMeta: `confidence ${verifierAction ? verifierAction.confidence.toFixed(2) : "N/A"} · raw data only`,
    },
    {
      title: "Derive action",
      diverge: amountDiverge,
      wBody: workerAction ? `${describeOutcome(workerAction)}.` : "No action proposed yet.",
      wMeta: `action_type=${workerAction?.action_type ?? "N/A"} target_transaction=${workerAction?.target_transaction_id ?? "none"} target_subscription=${workerAction?.target_subscription_id ?? "none"}`,
      vBody: verifierAction ? `${describeOutcome(verifierAction)}.` : "No independent conclusion recorded.",
      vMeta: `action_type=${verifierAction?.action_type ?? "N/A"} target_transaction=${verifierAction?.target_transaction_id ?? "none"} target_subscription=${verifierAction?.target_subscription_id ?? "none"}`,
    },
  ];
}

function padStep(n: number): string {
  return String(n).padStart(2, "0");
}

// What each decision button should actually say it will do, per action_type.
function actionButtonCopy(action: ProposedAction): { verb: string; meta: string } {
  switch (action.action_type) {
    case "refund":
    case "partial_refund":
      return { verb: `refund of ${formatMoney(action.amount, action.currency)}`, meta: `target ${action.target_transaction_id ?? "N/A"}` };
    case "cancel_subscription":
      return { verb: "cancel the subscription", meta: `target ${action.target_subscription_id ?? "N/A"}` };
    case "apply_account_credit":
      return { verb: `credit ${formatMoney(action.amount, action.currency)} to the account`, meta: "customer balance" };
    case "flag_for_fraud_review":
      return { verb: "flag for fraud review", meta: "no money moved" };
    case "no_action":
      return { verb: "take no action", meta: "no money moved" };
    default:
      return { verb: "escalate to a human", meta: "no money moved" };
  }
}

function clock(iso: string): string {
  return new Date(iso).toISOString().slice(11, 23);
}

function timeDelta(prevIso: string | undefined, curIso: string): string {
  if (!prevIso) return "N/A";
  const ms = new Date(curIso).getTime() - new Date(prevIso).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function Collapsible({
  label,
  hint,
  defaultOpen,
  children,
}: {
  label: string;
  hint: string;
  defaultOpen: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-divider">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-[22px] py-3 text-left font-data text-[11.5px] tracking-[0.04em] text-neutral-600 hover:text-text"
      >
        <span>
          {open ? "▾" : "▸"} {label}
        </span>
        <span className="text-neutral-500">{hint}</span>
      </button>
      {open && <div className="border-t border-divider">{children}</div>}
    </div>
  );
}

function AuditTrail({ trail }: { trail: AuditRecord[] }) {
  return (
    <div className="px-[22px] py-[18px]">
      {trail.map((event, i) => {
        const actor = eventActor(event.event_type);
        const flagged = eventFlagged(event.event_type, event.detail);
        const summary = eventSummary(event.event_type, event.detail);
        const payload = eventPayload(event.event_type, event.detail);
        return (
          <div
            key={event.id}
            className="grid grid-cols-[112px_28px_1fr_72px] gap-[14px] border-b border-divider px-1 py-[13px] last:border-b-0"
            style={{ background: flagged ? "color-mix(in srgb, var(--color-danger) 8%, transparent)" : "transparent" }}
          >
            <div className="pt-0.5 font-data text-[11.5px] text-neutral-600">{clock(event.created_at)}</div>
            <div className="flex justify-center pt-[3px]">
              <div
                className="h-[9px] w-[9px]"
                style={{
                  background: flagged ? "var(--color-danger)" : actor === "verifier" ? "var(--color-accent)" : "var(--color-neutral-400)",
                }}
              />
            </div>
            <div>
              <div className="flex flex-wrap items-baseline gap-2.5">
                <span
                  className="font-data px-[5px] py-px text-[10px] tracking-[0.1em]"
                  style={{
                    color: flagged ? "#fff" : "var(--color-neutral-100)",
                    background: flagged ? "var(--color-danger)" : actor === "verifier" ? "var(--color-accent)" : "var(--color-neutral-600)",
                  }}
                >
                  {actor.toUpperCase()}
                </span>
                <span className="font-data text-[12.5px] font-medium" style={{ color: flagged ? "var(--color-danger)" : "var(--color-text)" }}>
                  {eventName(event.event_type)}
                </span>
              </div>
              <div className="mt-[3px] max-w-[820px] text-[13.5px] text-neutral-800">{summary}</div>
              {payload && <div className="font-data mt-1 text-[11px] text-neutral-600">{payload}</div>}
            </div>
            <div className="pt-0.5 text-right font-data text-[11px] text-neutral-600">{timeDelta(trail[i - 1]?.created_at, event.created_at)}</div>
          </div>
        );
      })}
    </div>
  );
}

function ReasoningTrail({
  blockedByPolicy,
  steps,
}: {
  blockedByPolicy: boolean;
  steps: Step[];
}) {
  if (blockedByPolicy) {
    return (
      <div className="px-[22px] py-5">
        <div className="heading-label text-[15.5px]">Worker reasoning</div>
        <div className="font-data mt-0.5 mb-3 text-[10.5px] text-neutral-600">worker/4.2 · reads ticket + tool output · proposes action</div>
        {steps.slice(0, 3).map((s) => (
          <div key={s.title} className="mb-4">
            <div className="heading-label text-[13.5px] text-neutral-700">{s.title}</div>
            <div className="mt-1 text-[15px] leading-[1.6] text-neutral-800">{s.wBody}</div>
            <div className="font-data mt-1 text-[11px] text-neutral-600">{s.wMeta}</div>
          </div>
        ))}
        <div className="mt-5 border-t-2 border-divider pt-4" style={{ borderColor: SIG }}>
          <div className="heading-label text-[13.5px]" style={{ color: SIG }}>
            Verifier did not run
          </div>
          <div className="mt-1 text-[14.5px] leading-[1.6] text-neutral-800">
            The policy gate blocked execution before independent verification began. The verifier never gets a
            chance to weigh in once OPA denies or escalates.
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="grid grid-cols-[1fr_100px_1fr] border-b border-divider bg-neutral-100">
        <div className="border-r border-divider px-[22px] py-3">
          <div className="heading-label text-[16px] tracking-[0.04em]">Worker agent</div>
          <div className="font-data mt-0.5 text-[10.5px] text-neutral-600">worker/4.2 · reads ticket + tool output · proposes action</div>
        </div>
        <div className="flex items-center justify-center border-r border-divider font-data text-[9px] tracking-[0.1em] text-neutral-600">
          CROSS-CHECK
        </div>
        <div className="px-[22px] py-3">
          <div className="heading-label text-[16px] tracking-[0.04em]">Verifier agent</div>
          <div className="font-data mt-0.5 text-[10.5px] text-neutral-600">verifier/1.8 · raw data only · never sees the worker&apos;s summary</div>
        </div>
      </div>

      <div className="grid grid-cols-[1fr_100px_1fr] items-stretch">
        {steps.map((s, i) => {
          const bad = Boolean(s.diverge);
          const pane = (side: "w" | "v"): CSSProperties => ({
            padding: "18px 22px",
            borderBottom: "1px solid var(--color-divider)",
            borderRight: side === "w" ? "1px solid var(--color-divider)" : "0",
            background: bad ? `color-mix(in srgb, ${SIG} 9%, transparent)` : "transparent",
            boxShadow: bad ? (side === "w" ? `inset -4px 0 0 0 ${SIG}` : `inset 4px 0 0 0 ${SIG}`) : "none",
          });
          return (
            <div key={s.title} className="contents">
              <div style={pane("w")}>
                <div className="flex items-baseline gap-2">
                  <span className="font-data text-[10px] text-neutral-500">W·{padStep(i + 1)}</span>
                  <span className="heading-label text-[14px]" style={{ color: bad ? SIG : "var(--color-neutral-700)" }}>
                    {s.title}
                  </span>
                </div>
                <div
                  className="text-pretty mt-2"
                  style={{ fontSize: bad ? "16px" : "15px", lineHeight: 1.65, color: bad ? SIG : "var(--color-text)", fontWeight: bad ? 500 : 400 }}
                >
                  {s.wBody}
                </div>
                <div className="font-data mt-2 text-[10.5px] text-neutral-500">{s.wMeta}</div>
              </div>
              <div className="flex flex-col items-center justify-center gap-1 border-r border-b border-divider py-4">
                {bad ? (
                  <span
                    className="font-data px-1.5 py-1 text-center text-[9px] leading-tight tracking-[0.06em] text-white"
                    style={{ background: SIG, animation: "bsSeam 1.3s ease-out infinite" }}
                  >
                    BREAK
                  </span>
                ) : (
                  <span className="font-data text-[13px] text-neutral-400">✓</span>
                )}
              </div>
              <div style={pane("v")}>
                <div className="flex items-baseline gap-2">
                  <span className="font-data text-[10px] text-neutral-500">V·{padStep(i + 1)}</span>
                  <span className="heading-label text-[14px]" style={{ color: bad ? SIG : "var(--color-neutral-700)" }}>
                    {s.title}
                  </span>
                </div>
                <div
                  className="text-pretty mt-2"
                  style={{ fontSize: bad ? "16px" : "15px", lineHeight: 1.65, color: bad ? SIG : "var(--color-text)", fontWeight: bad ? 500 : 400 }}
                >
                  {s.vBody}
                </div>
                <div className="font-data mt-2 text-[10.5px] text-neutral-500">{s.vMeta}</div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

export function TicketDetailClient({
  detail: initialDetail,
  zendeskSubdomain,
  canDecide,
}: {
  detail: TicketDetail | null;
  zendeskSubdomain: string | null;
  canDecide: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  // Live-polls this ticket so the pipeline stage, summary, and reasoning
  // steps update on their own as the pipeline progresses.
  const [detail, refetch] = usePolling<TicketDetail | null>(`/api/tickets/${initialDetail?.ticket.id ?? "missing"}`, initialDetail);

  if (!detail) {
    return (
      <div className="flex flex-1 items-center px-[22px] py-8">
        <p className="font-data text-sm text-neutral-500">Ticket not found.</p>
      </div>
    );
  }

  const { ticket, policyDecision, verificationResult, verifierAction, auditTrail } = detail;
  const workerAction = ticket.proposed_action;
  const stillPending = ticket.status === "escalated" || ticket.status === "blocked";
  const blockedByPolicy = policyDecision != null && policyDecision.decision !== "allow" && verificationResult == null;
  const consistent = verificationResult?.consistent ?? false;
  const amountDiverge = !blockedByPolicy && verificationResult != null && !consistent;
  // The only case with a genuine choice between two different conclusions.
  // Every other "needs a human" case gets exactly one button.
  const hasRealChoice = amountDiverge;
  const humanResolved = auditTrail.some((e) => e.event_type === "human_decision");
  const isResolved = ticket.status === "resolved";
  const isInProgress = !stillPending && !isResolved;

  const bothMonetary =
    workerAction != null && verifierAction != null && isMonetary(workerAction.action_type) && isMonetary(verifierAction.action_type);
  const deltaAmount = bothMonetary ? Math.abs(workerAction!.amount - verifierAction!.amount) : null;

  const steps = buildSteps(ticket, workerAction, verifierAction, verificationResult?.verifier_rationale, auditTrail, amountDiverge);

  async function decide(decision: "approve_verifier" | "approve_worker" | "hold", chosenAction: typeof workerAction) {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch(`/api/tickets/${ticket.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, chosenAction }),
      });
      const json = await res.json();
      setResult(json.error ? `Failed: ${json.error}` : `Recorded: ticket now ${json.status}`);
      await refetch();
    } finally {
      setBusy(false);
    }
  }

  // Decision summary: the first thing a human reads. Everything below (the
  // full step-by-step trail) is supporting evidence, not the headline.
  const summary = (() => {
    if (blockedByPolicy) {
      return {
        badge: "BLOCKED",
        headline: "Blocked before a human check even ran",
        why: policyDecision?.reason ?? "The policy gate stopped this before the verifier could weigh in.",
        recommended: workerAction ? `Recommended: ${actionButtonCopy(workerAction).verb}.` : null,
        tone: "danger" as const,
      };
    }
    if (amountDiverge) {
      // verificationResult.notes is Python-generated audit-trail text, not
      // plain language, so compose a plain sentence instead.
      const why =
        workerAction && verifierAction
          ? `Worker proposed ${describeOutcome(workerAction)}. The verifier independently concluded ${describeOutcome(verifierAction)}.`
          : (verificationResult?.notes ?? "The two independent conclusions don't match.");
      return {
        badge: "DIVERGENCE",
        headline: "Worker and verifier disagree on how to resolve this",
        why,
        recommended: "Recommended: review both conclusions below and choose one.",
        tone: "danger" as const,
      };
    }
    if (stillPending && consistent) {
      return {
        badge: "NEEDS REVIEW",
        headline: "Both agents agree: this needs a human",
        why: firstSentence(workerAction?.rationale, "Neither agent had enough information to act automatically."),
        recommended: workerAction ? `Recommended: ${actionButtonCopy(workerAction).verb}.` : null,
        tone: "accent" as const,
      };
    }
    if (isResolved && humanResolved) {
      return {
        badge: "RESOLVED",
        headline: "Resolved by a human decision",
        why: workerAction ? `Chosen: ${describeOutcome(workerAction)}.` : "Resolved.",
        recommended: null,
        tone: "positive" as const,
      };
    }
    if (isResolved) {
      return {
        badge: "RESOLVED",
        headline: "Resolved automatically",
        why: "Worker and verifier independently agreed, so this executed without a human.",
        recommended: null,
        tone: "positive" as const,
      };
    }
    if (isInProgress) {
      return {
        badge: "IN PROGRESS",
        headline: statusLabel(ticket.status, ticket.policy_blocked),
        why: "The pipeline is still running, this page updates on its own.",
        recommended: null,
        tone: "neutral" as const,
      };
    }
    return {
      badge: "CROSS-CHECKING",
      headline: "Verifier re-deriving independently…",
      why: "No conflict recorded yet.",
      recommended: null,
      tone: "neutral" as const,
    };
  })();

  const toneColor = { danger: SIG, accent: "var(--color-accent)", positive: "var(--color-accent)", neutral: "var(--color-neutral-600)" }[
    summary.tone
  ];
  const isAlert = summary.tone === "danger";

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto">
      <Breadcrumb ticketLabel={`BSP-${ticket.zendesk_ticket_id}`} />

      {/* Ticket info header */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-0 border-b border-divider bg-neutral-100">
        <div className="border-r border-divider px-[22px] py-3.5">
          <div className="font-data text-[10px] tracking-[0.1em] text-neutral-600">CUSTOMER</div>
          <div className="mt-1 text-[14px] text-text">{ticket.customer_email}</div>
          <div className="font-data mt-0.5 text-[11px] text-neutral-600">{ticket.customer_id ?? "unresolved"}</div>
        </div>
        <div className="border-r border-divider px-[22px] py-3.5">
          <div className="font-data text-[10px] tracking-[0.1em] text-neutral-600">TICKET</div>
          <div className="mt-1 font-data text-[14px] text-text">
            BSP-{ticket.zendesk_ticket_id} · zendesk#{ticket.zendesk_ticket_id}
          </div>
          <div className="font-data mt-0.5 text-[11px] text-neutral-600">
            created <Age fromIso={ticket.created_at} /> ago
          </div>
          {zendeskSubdomain && (
            // Deep-links straight to the agent view, same as any normal
            // Zendesk agent link.
            <a
              href={`https://${zendeskSubdomain}.zendesk.com/agent/tickets/${ticket.zendesk_ticket_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-data mt-1 inline-block text-[11px] text-accent hover:underline"
            >
              Open in Zendesk ↗
            </a>
          )}
        </div>
        <div className="border-r border-divider px-[22px] py-3.5">
          <div className="font-data text-[10px] tracking-[0.1em] text-neutral-600">CLASSIFICATION</div>
          {ticket.classification ? (
            <>
              <div className="mt-1 text-[14px] text-text capitalize">{ticket.classification.intent.replace(/_/g, " ")}</div>
              <div className="font-data mt-0.5 text-[11px] text-neutral-600">
                urgency={ticket.classification.urgency} · billing_relevant={String(ticket.classification.is_billing_relevant)}
              </div>
            </>
          ) : (
            <div className="mt-1 text-[13px] text-neutral-500">Not yet classified</div>
          )}
        </div>
        <div className="px-[22px] py-3.5">
          <div className="font-data text-[10px] tracking-[0.1em] text-neutral-600">STATUS</div>
          <div className="mt-1 text-[14px] text-text">{statusLabel(ticket.status, ticket.policy_blocked)}</div>
          <div className="font-data mt-0.5 text-[11px] text-neutral-600">updated {new Date(ticket.updated_at).toLocaleString()}</div>
        </div>
      </div>

      {/* Customer's message: always visible, not buried in the collapsed trail. */}
      <div className="border-b border-divider px-[22px] py-4">
        <div className="font-data text-[10px] tracking-[0.1em] text-neutral-600">CUSTOMER&apos;S MESSAGE</div>
        <p className="mt-1.5 max-w-[760px] text-[15px] leading-[1.6] text-text">{ticket.ticket_text || "No ticket body recorded."}</p>
      </div>

      {/* Decision summary: the primary thing a human reads first */}
      <div
        className="px-[22px] py-[18px]"
        style={{
          borderBottom: isAlert ? `2px solid ${SIG}` : "1px solid var(--color-divider)",
          background: isAlert ? `color-mix(in srgb, ${SIG} 12%, transparent)` : "var(--color-neutral-100)",
        }}
      >
        <div className="flex flex-wrap items-center gap-[14px]">
          <span
            className="font-data px-2 py-1 text-[11px] tracking-[0.12em]"
            style={
              isAlert
                ? { color: "#fff", background: toneColor, animation: "bsPulse 1.5s ease-in-out infinite" }
                : // Non-alert badges are outlined, not filled, so the primary
                  // action button is the only solid-filled accent on the page.
                  { color: toneColor, background: "transparent", border: `1px solid ${toneColor}` }
            }
          >
            {summary.badge}
          </span>
          <span className="font-heading text-[24px] tracking-[0.01em]">{summary.headline}</span>
        </div>
        <p className="mt-2.5 max-w-[760px] text-[14.5px] leading-[1.55] text-neutral-800">{summary.why}</p>
        {summary.recommended && <p className="mt-1.5 text-[14px] font-medium leading-[1.5] text-text">{summary.recommended}</p>}
      </div>

      {/* Decision actions: right under the summary, not buried at the bottom */}
      {stillPending && !canDecide && (
        <div className="border-b border-divider bg-neutral-100 px-[22px] py-[14px]">
          <span className="font-data text-[11.5px] text-neutral-700">
            Read-only access. Approving, overriding or holding needs an approver or admin.
          </span>
        </div>
      )}
      {stillPending && canDecide && (
        <div className="flex flex-wrap items-center gap-2.5 border-b border-divider bg-neutral-100 px-[22px] py-[14px]">
          {hasRealChoice ? (
            <>
              {!blockedByPolicy &&
                verifierAction &&
                (() => {
                  const copy = actionButtonCopy(verifierAction);
                  return (
                    <button
                      disabled={busy}
                      onClick={() => decide("approve_verifier", verifierAction)}
                      className="btn btn-primary blueprint flex-col items-start gap-px px-[18px] py-2 text-left"
                    >
                      <BlueprintCorners />
                      <span>Override: {copy.verb}</span>
                      <span className="font-data text-[10px] font-normal tracking-[0.06em] opacity-85">
                        VERIFIER&apos;S CONCLUSION · REJECTS WORKER&apos;S {workerAction ? actionButtonCopy(workerAction).verb : "N/A"}
                      </span>
                    </button>
                  );
                })()}
              {workerAction &&
                (() => {
                  const copy = actionButtonCopy(workerAction);
                  return (
                    <button
                      disabled={busy}
                      onClick={() => decide("approve_worker", workerAction)}
                      className="btn btn-secondary flex-col items-start gap-px px-4 py-2 text-left"
                    >
                      <span>Approve: {copy.verb}</span>
                      <span className="font-data text-[10px] font-normal tracking-[0.06em] text-neutral-600">WORKER&apos;S CONCLUSION · {copy.meta}</span>
                    </button>
                  );
                })()}
            </>
          ) : (
            workerAction && (
              <button
                disabled={busy}
                onClick={() => decide("approve_worker", workerAction)}
                className="btn btn-primary blueprint flex-col items-start gap-px px-[18px] py-2 text-left"
              >
                <BlueprintCorners />
                <span>Acknowledge: {actionButtonCopy(workerAction).verb}</span>
                <span className="font-data text-[10px] font-normal tracking-[0.06em] opacity-85">
                  {blockedByPolicy ? "WORKER'S CONCLUSION" : "WORKER AND VERIFIER AGREE"} · {actionButtonCopy(workerAction).meta}
                </span>
              </button>
            )
          )}
          <button disabled={busy} onClick={() => decide("hold", null)} className="btn btn-ghost">
            Hold for human ops
          </button>
          {result && <span className="font-data text-[11.5px] text-neutral-700">{result}</span>}
        </div>
      )}

      {/* Divergence delta: kept visible (not collapsed), it's the key evidence for the decision above */}
      {workerAction && verifierAction && amountDiverge && (
        <div style={{ borderBottom: `2px solid ${SIG}`, background: `color-mix(in srgb, ${SIG} 9%, transparent)` }}>
          <div className="grid grid-cols-[1fr_100px_1fr] items-center">
            <div className="px-[22px] py-4 text-right">
              <div className="font-data text-[10px] tracking-[0.1em]" style={{ color: SIG }}>
                WORKER {workerAction.action_type.replace(/_/g, " ").toUpperCase()}
              </div>
              <div className="font-data text-[28px] leading-[1.15] font-medium" style={{ color: SIG }}>
                {describeOutcome(workerAction)}
              </div>
              <div className="text-[12.5px] text-neutral-700">{firstSentence(workerAction.rationale, "")}</div>
            </div>
            <div className="flex flex-col items-center gap-1">
              <div className="font-data text-[9.5px] tracking-[0.1em]" style={{ color: SIG }}>
                {bothMonetary ? "DELTA" : "VS"}
              </div>
              <div className="font-data text-[15px]" style={{ color: SIG }}>
                {deltaAmount != null ? formatMoney(deltaAmount, workerAction.currency) : "≠"}
              </div>
            </div>
            <div className="px-[22px] py-4">
              <div className="font-data text-[10px] tracking-[0.1em]" style={{ color: SIG }}>
                VERIFIER {verifierAction.action_type.replace(/_/g, " ").toUpperCase()}
              </div>
              <div className="font-data text-[28px] leading-[1.15] font-medium" style={{ color: SIG }}>
                {describeOutcome(verifierAction)}
              </div>
              <div className="text-[12.5px] text-neutral-700">{firstSentence(verifierAction.rationale, "")}</div>
            </div>
          </div>
        </div>
      )}

      {/* Policy gate: plain language first, technical detail secondary */}
      <div className="border-b border-divider px-[22px] py-4">
        <div className="heading-label text-[13.5px] text-neutral-700">Policy gate</div>
        {policyDecision ? (
          <>
            <div className="mt-1 text-[14.5px] text-text">
              {policyDecision.decision === "allow"
                ? "Passed. No policy limits were hit."
                : `${policyDecision.decision === "deny" ? "Denied" : "Escalated"}${policyDecision.reason ? `: ${policyDecision.reason}` : ""}`}
            </div>
            <div className="font-data mt-1 text-[11px] text-neutral-600">
              {policyDecision.matched_rule ? `policy.${policyDecision.matched_rule}` : "no rule matched"}
              {!blockedByPolicy && verificationResult && ` · verifier verdict=${consistent ? "MATCH" : "MISMATCH"}`}
            </div>
          </>
        ) : (
          <div className="mt-1 text-[13px] text-neutral-500">Policy gate has not run yet.</div>
        )}
      </div>

      {/* Full reasoning trail: collapsed by default except for genuine
          divergence, where it's the actual evidence for the decision above. */}
      <Collapsible
        label="Show full reasoning trail (worker vs. verifier)"
        hint={blockedByPolicy ? "worker only, verifier never ran" : `${steps.length} steps compared`}
        defaultOpen={amountDiverge}
      >
        <ReasoningTrail blockedByPolicy={blockedByPolicy} steps={steps} />
      </Collapsible>

      <Collapsible label={`Full event log (${auditTrail.length} events)`} hint="immutable audit trail" defaultOpen={false}>
        <AuditTrail trail={auditTrail} />
      </Collapsible>

      {!stillPending && (
        <div className="flex flex-none items-center border-t border-divider bg-neutral-100 px-[22px] py-[14px] font-data text-[11.5px] tracking-[0.03em] text-neutral-700">
          {result ?? `Resolved: ticket status: ${ticket.status}`}
        </div>
      )}
    </div>
  );
}
