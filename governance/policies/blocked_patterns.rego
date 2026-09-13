package backstop.governance.blocked_patterns

import data.data as policy_data

refundable_action_types := {"refund", "partial_refund"}

# violation is a set of {rule, reason} objects, each entry is one hard block.
violation contains {"rule": "blocked_patterns.blocked_customer", "reason": reason} if {
	input.customer.id in policy_data.blocked_patterns.blocked_customer_ids
	reason := sprintf("Customer %s is on the blocked customer list", [input.customer.id])
}

violation contains {"rule": "blocked_patterns.disputed_chargeback", "reason": reason} if {
	policy_data.blocked_patterns.blocked_action_on_disputed_chargeback
	input.transaction.is_disputed == true
	input.action.action_type in refundable_action_types
	reason := sprintf(
		"Transaction %s is under an active dispute/chargeback, refunds are blocked pending resolution",
		[input.transaction.id],
	)
}

violation contains {"rule": "blocked_patterns.fraud_flag", "reason": reason} if {
	input.customer.has_fraud_flag == true
	reason := sprintf("Customer %s has an active fraud flag", [input.customer.id])
}
