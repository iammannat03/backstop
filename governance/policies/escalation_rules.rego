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

# Fail-safe, not a business rule about refunds specifically: if action_type is
# something no other policy file has an explicit opinion on, OPA's default
# decision is "allow" (main.rego), that's the wrong default for an action type
# nobody has reviewed the risk shape of yet. This flag turns "unrecognized" into
# "escalate" by construction, so a new action_type is safe-by-default the moment
# it's added to the worker's vocabulary, even before policy authors get around
# to writing a dedicated rule for it.
flag contains {"rule": "escalation_rules.unrecognized_action_type", "reason": reason} if {
	not input.action.action_type in policy_data.known_action_types
	reason := sprintf(
		"action_type %q is not in the known action-type list, routing to a human until policy is updated",
		[input.action.action_type],
	)
}

# flag_for_fraud_review is categorical: a human/fraud team must always see this,
# regardless of the worker's confidence. Unlike ambiguous_intent below, there is
# no confidence level at which auto-proceeding on a fraud signal is acceptable.
flag contains {"rule": "escalation_rules.fraud_review_requested", "reason": "Ticket flagged for fraud review, always routed to a human, regardless of confidence."} if {
	input.action.action_type == "flag_for_fraud_review"
}
