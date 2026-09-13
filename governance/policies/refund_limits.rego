package backstop.governance.refund_limits

import data.data as policy_data

refundable_action_types := {"refund", "partial_refund"}

# violation is a set of {rule, reason} objects, each entry is one hard-limit breach.
violation contains {"rule": "refund_limits.max_single_refund_exceeded", "reason": reason} if {
	input.action.action_type in refundable_action_types
	currency := input.action.currency
	max_amount := policy_data.refund_limits.max_single_refund_cents[currency]
	input.action.amount > max_amount
	reason := sprintf(
		"Refund amount %d (%s) exceeds max single refund limit of %d (%s)",
		[input.action.amount, currency, max_amount, currency],
	)
}

violation contains {"rule": "refund_limits.monthly_refund_cap_exceeded", "reason": reason} if {
	input.action.action_type in refundable_action_types
	max_refunds := policy_data.refund_limits.max_refunds_per_customer_per_month
	input.customer.refunds_last_30_days >= max_refunds
	reason := sprintf(
		"Customer %s has already had %d refund(s) in the last 30 days (limit: %d)",
		[input.customer.id, input.customer.refunds_last_30_days, max_refunds],
	)
}

# Same shape as max_single_refund_exceeded above, applied to apply_account_credit
# instead. A goodwill credit is still money leaving the business, just via a
# different Stripe mechanism, so it gets the same kind of hard dollar cap.
violation contains {"rule": "refund_limits.max_account_credit_exceeded", "reason": reason} if {
	input.action.action_type == "apply_account_credit"
	currency := input.action.currency
	max_amount := policy_data.credit_limits.max_account_credit_cents[currency]
	input.action.amount > max_amount
	reason := sprintf(
		"Account credit amount %d (%s) exceeds max single credit limit of %d (%s)",
		[input.action.amount, currency, max_amount, currency],
	)
}
