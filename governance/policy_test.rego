package backstop.governance_test

import data.backstop.governance

# Tests use a mocked policy_data document (via `with`) rather than the real
# governance/data/policy_data.json, so they stay stable if the real limits change.
mock_policy_data := {
	"known_action_types": [
		"refund", "partial_refund", "no_action", "escalate",
		"cancel_subscription", "apply_account_credit", "flag_for_fraud_review",
	],
	"refund_limits": {
		"max_single_refund_cents": {"usd": 20000, "eur": 18000, "gbp": 16000},
		"max_refunds_per_customer_per_month": 3,
	},
	"credit_limits": {"max_account_credit_cents": {"usd": 20000, "eur": 18000, "gbp": 16000}},
	"escalation": {
		"ambiguous_intent_confidence_threshold": 0.6,
		"repeat_customer_ticket_threshold": 3,
		"high_value_transaction_cents": {"usd": 50000, "eur": 45000, "gbp": 40000},
	},
	"blocked_patterns": {
		"blocked_customer_ids": ["cus_fraud_blocked_1"],
		"blocked_action_on_disputed_chargeback": true,
	},
}

base_action := {
	"action_type": "refund",
	"amount": 5000,
	"currency": "usd",
	"target_transaction_id": "ch_test_1",
	"confidence": 0.9,
}

base_customer := {
	"id": "cus_normal_1",
	"is_blocked": false,
	"has_fraud_flag": false,
	"refunds_last_30_days": 0,
	"ticket_count_last_90_days": 1,
}

base_transaction := {
	"id": "ch_test_1",
	"amount": 5000,
	"currency": "usd",
	"is_disputed": false,
}

base_input := {"action": base_action, "customer": base_customer, "transaction": base_transaction}

# --- clean case ---

test_clean_refund_allowed if {
	result := governance.result with input as base_input with data.data as mock_policy_data
	result.decision == "allow"
	result.matched_rule == null
}

# --- refund_limits ---

test_refund_exceeds_max_amount_denied if {
	req := object.union(base_input, {"action": object.union(base_action, {"amount": 25000})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "deny"
	result.matched_rule == "refund_limits.max_single_refund_exceeded"
}

test_monthly_refund_cap_denied if {
	req := object.union(base_input, {"customer": object.union(base_customer, {"refunds_last_30_days": 3})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "deny"
	result.matched_rule == "refund_limits.monthly_refund_cap_exceeded"
}

test_non_refund_action_ignores_refund_limits if {
	# an informational reply shouldn't trip refund-amount checks even with a huge "amount" field
	req := object.union(base_input, {"action": object.union(base_action, {"action_type": "no_action", "amount": 999999})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "allow"
}

test_account_credit_within_limit_allowed if {
	req := object.union(base_input, {"action": object.union(base_action, {"action_type": "apply_account_credit", "amount": 5000, "target_transaction_id": ""})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "allow"
}

test_account_credit_exceeds_max_denied if {
	req := object.union(base_input, {"action": object.union(base_action, {"action_type": "apply_account_credit", "amount": 25000, "target_transaction_id": ""})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "deny"
	result.matched_rule == "refund_limits.max_account_credit_exceeded"
}

# --- blocked_patterns ---

test_blocked_customer_denied if {
	req := object.union(base_input, {"customer": object.union(base_customer, {"id": "cus_fraud_blocked_1"})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "deny"
	result.matched_rule == "blocked_patterns.blocked_customer"
}

test_disputed_chargeback_refund_denied if {
	req := object.union(base_input, {"transaction": object.union(base_transaction, {"is_disputed": true})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "deny"
	result.matched_rule == "blocked_patterns.disputed_chargeback"
}

test_fraud_flagged_customer_denied if {
	req := object.union(base_input, {"customer": object.union(base_customer, {"has_fraud_flag": true})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "deny"
	result.matched_rule == "blocked_patterns.fraud_flag"
}

# --- escalation_rules ---

test_ambiguous_intent_escalated if {
	req := object.union(base_input, {"action": object.union(base_action, {"confidence": 0.3})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "escalate"
	result.matched_rule == "escalation_rules.ambiguous_intent"
}

test_repeat_customer_escalated if {
	req := object.union(base_input, {"customer": object.union(base_customer, {"ticket_count_last_90_days": 5})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "escalate"
	result.matched_rule == "escalation_rules.repeat_customer"
}

test_high_value_transaction_escalated if {
	req := object.union(base_input, {"transaction": object.union(base_transaction, {"amount": 60000})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "escalate"
	result.matched_rule == "escalation_rules.high_value_transaction"
}

test_unrecognized_action_type_escalated if {
	# a made-up/future action_type nobody has written a policy rule for yet must
	# never fall through to the "allow" default, see escalation_rules.rego.
	req := object.union(base_input, {"action": object.union(base_action, {"action_type": "teleport_customer"})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "escalate"
	result.matched_rule == "escalation_rules.unrecognized_action_type"
}

test_fraud_review_always_escalated_even_at_high_confidence if {
	# flag_for_fraud_review is categorical, high confidence must not bypass it,
	# unlike ambiguous_intent which is confidence-gated.
	req := object.union(base_input, {"action": object.union(base_action, {"action_type": "flag_for_fraud_review", "confidence": 0.99})})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "escalate"
	result.matched_rule == "escalation_rules.fraud_review_requested"
}

# --- precedence ---

test_hard_violation_takes_precedence_over_escalation if {
	# blocked customer AND ambiguous intent at once -- "deny" must win over "escalate"
	req := object.union(base_input, {
		"customer": object.union(base_customer, {"id": "cus_fraud_blocked_1"}),
		"action": object.union(base_action, {"confidence": 0.2}),
	})
	result := governance.result with input as req with data.data as mock_policy_data
	result.decision == "deny"
	result.matched_rule == "blocked_patterns.blocked_customer"
	count(result.all_matched_rules) == 2
}
