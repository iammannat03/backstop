package backstop.governance.escalation_rules

import data.data as policy_data

# flag is a set of {rule, reason} objects, soft signals that a human should look
# at this before it executes, even though nothing here violates a hard boundary.
flag contains {"rule": "escalation_rules.ambiguous_intent", "reason": reason} if {
	threshold := policy_data.escalation.ambiguous_intent_confidence_threshold
	input.action.confidence < threshold
	reason := sprintf(
		"Worker confidence %.2f is below the ambiguous-intent threshold %.2f",
		[input.action.confidence, threshold],
	)
}

flag contains {"rule": "escalation_rules.repeat_customer", "reason": reason} if {
	threshold := policy_data.escalation.repeat_customer_ticket_threshold
	input.customer.ticket_count_last_90_days >= threshold
	reason := sprintf(
		"Customer %s has filed %d ticket(s) in the last 90 days (repeat-customer threshold: %d)",
		[input.customer.id, input.customer.ticket_count_last_90_days, threshold],
	)
}

flag contains {"rule": "escalation_rules.high_value_transaction", "reason": reason} if {
	currency := input.transaction.currency
	threshold := policy_data.escalation.high_value_transaction_cents[currency]
	input.transaction.amount >= threshold
	reason := sprintf(
		"Transaction amount %d (%s) meets/exceeds the high-value threshold %d (%s)",
		[input.transaction.amount, currency, threshold, currency],
	)
}

# Fail-safe: an action_type no other policy has an opinion on escalates
# instead of silently falling through to the "allow" default.
flag contains {"rule": "escalation_rules.unrecognized_action_type", "reason": reason} if {
	not input.action.action_type in policy_data.known_action_types
	reason := sprintf(
		"action_type %q is not in the known action-type list, routing to a human until policy is updated",
		[input.action.action_type],
	)
}

# Categorical: always escalates regardless of confidence.
flag contains {"rule": "escalation_rules.fraud_review_requested", "reason": "Ticket flagged for fraud review, always routed to a human, regardless of confidence."} if {
	input.action.action_type == "flag_for_fraud_review"
}
