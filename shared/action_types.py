"""Single source of truth for valid action_type strings. Keep in sync with
shared.models.ActionType and governance/data/policy_data.json's known_action_types.
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
