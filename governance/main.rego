package backstop.governance

import data.backstop.governance.blocked_patterns
import data.backstop.governance.escalation_rules
import data.backstop.governance.refund_limits

# Precedence: deny (hard violation) > escalate (soft flag) > allow.
# Only "allow" continues to the verifier; deny/escalate both go straight to a human.

hard_violations := sort(array.concat(
	[v | some v in blocked_patterns.violation],
	[v | some v in refund_limits.violation],
))

escalation_flags := sort([f | some f in escalation_rules.flag])

default decision := "allow"

decision := "deny" if count(hard_violations) > 0

decision := "escalate" if {
	count(hard_violations) == 0
	count(escalation_flags) > 0
}

default matched_rule := null

matched_rule := hard_violations[0].rule if decision == "deny"

matched_rule := escalation_flags[0].rule if decision == "escalate"

default reason := null

reason := hard_violations[0].reason if decision == "deny"

reason := escalation_flags[0].reason if decision == "escalate"

# Every rule that fired, not just the first (matched_rule), for the audit trail.
all_matched_rules := array.concat(
	[v.rule | some v in hard_violations],
	[f.rule | some f in escalation_flags],
)

result := {
	"decision": decision,
	"matched_rule": matched_rule,
	"reason": reason,
	"all_matched_rules": all_matched_rules,
}
