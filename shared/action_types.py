"""The single source of truth for the set of action_type strings the worker's
reasoning engine and the verifier's cross-reference engine are allowed to
produce. Both `worker_agent/reasoning_engine.py` and
`verifier_agent/cross_reference.py` independently build their own Gemini JSON
schema and system-instruction prose (that independence is deliberate, see
docs/rules.md) but there's no reason the plain list of valid enum *values*
should be hand-copied in both places too; that's just an accident of
copy-paste that risks the two schemas silently drifting apart as the
vocabulary grows. Import ACTION_TYPE_VALUES into both schemas instead of
retyping the list.

Keep this in sync with `shared.models.ActionType` and
`governance/data/policy_data.json`'s `known_action_types`.
"""

ACTION_TYPE_VALUES: list[str] = [
    "refund",
    "partial_refund",
    "no_action",
    "escalate",
    "cancel_subscription",
    "apply_account_credit",
    "flag_for_fraud_review",
]
