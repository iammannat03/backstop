package backstop.governance

import data.backstop.governance.blocked_patterns
import data.backstop.governance.escalation_rules
import data.backstop.governance.refund_limits

# Decision precedence:
#   1. blocked_patterns / refund_limits violations -> "deny"     (hard boundary, never allowed)
#   2. escalation_rules flags (no hard violation)   -> "escalate" (needs a human, not a violation)
#   3. otherwise                                    -> "allow"
#
# OPA never makes a judgment call here, every branch above is a deterministic threshold
# or list-membership check. See docs/rules.md for why "deny" and "escalate" both route to
# human escalation and skip the verifier, while only "allow" continues to it.

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

# every rule that fired, not just the first, kept for the audit trail so a ticket
# blocked for one reason doesn't hide that it also tripped a second, unrelated rule.
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
