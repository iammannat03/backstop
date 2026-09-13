// Minimal Stripe calls for the human approve/override path. Deliberately
// separate from the Python execution layer, and does not write back to
// Zendesk or Slack (a second OAuth token manager would race the Python one).

const STRIPE_API_KEY = process.env.STRIPE_API_KEY;

export async function issueRefund(
  ticketId: string,
  chargeId: string,
  amount: number,
): Promise<{ id: string; status: string }> {
  if (!STRIPE_API_KEY) {
    throw new Error("STRIPE_API_KEY not set");
  }

  const body = new URLSearchParams({ charge: chargeId, amount: String(amount) });
  const resp = await fetch("https://api.stripe.com/v1/refunds", {
    method: "POST",
    headers: {
      Authorization: `Basic ${Buffer.from(`${STRIPE_API_KEY}:`).toString("base64")}`,
      "Content-Type": "application/x-www-form-urlencoded",
      // Same idempotency convention as execution/stripe_executor.py, prefixed
      // distinctly so a human-approved refund can never collide with an
      // automated one for the same ticket.
      "Idempotency-Key": `backstop-human-refund-${ticketId}`,
    },
    body,
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Stripe refund failed (${resp.status}): ${text}`);
  }

  return resp.json();
}

export async function cancelSubscription(
  ticketId: string,
  subscriptionId: string,
): Promise<{ id: string; status: string }> {
  if (!STRIPE_API_KEY) {
    throw new Error("STRIPE_API_KEY not set");
  }

  const resp = await fetch(`https://api.stripe.com/v1/subscriptions/${subscriptionId}`, {
    method: "DELETE",
    headers: {
      Authorization: `Basic ${Buffer.from(`${STRIPE_API_KEY}:`).toString("base64")}`,
      "Idempotency-Key": `backstop-human-cancel-${ticketId}`,
    },
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Stripe subscription cancellation failed (${resp.status}): ${text}`);
  }

  return resp.json();
}

export async function applyAccountCredit(
  ticketId: string,
  customerId: string,
  amount: number,
  currency: string,
): Promise<{ id: string }> {
  if (!STRIPE_API_KEY) {
    throw new Error("STRIPE_API_KEY not set");
  }

  // Negative amount = credit against future invoices, same convention as
  // execution/stripe_executor.py's Python-side _apply_account_credit.
  const body = new URLSearchParams({ amount: String(-amount), currency });
  const resp = await fetch(`https://api.stripe.com/v1/customers/${customerId}/balance_transactions`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${Buffer.from(`${STRIPE_API_KEY}:`).toString("base64")}`,
      "Content-Type": "application/x-www-form-urlencoded",
      "Idempotency-Key": `backstop-human-credit-${ticketId}`,
    },
    body,
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Stripe account credit failed (${resp.status}): ${text}`);
  }

  return resp.json();
}
