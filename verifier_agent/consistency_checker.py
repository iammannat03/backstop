"""Consistency Checker: compares the worker's proposed action against the
verifier's own independently-derived conclusion. Only compares structured
fields (action_type, amount, target ids), never the worker's rationale prose.
"""

from shared.models import ProposedAction, VerificationResult

# Matches governance's escalation.ambiguous_intent_confidence_threshold.
_LOW_CONFIDENCE_THRESHOLD = 0.6


def check_consistency(worker_action: ProposedAction, verifier_action: ProposedAction) -> VerificationResult:
    fields_match = (
        worker_action.action_type == verifier_action.action_type
        and worker_action.amount == verifier_action.amount
        and worker_action.target_transaction_id == verifier_action.target_transaction_id
        and worker_action.target_subscription_id == verifier_action.target_subscription_id
    )
    low_confidence = verifier_action.confidence < _LOW_CONFIDENCE_THRESHOLD
    consistent = fields_match and not low_confidence

    if not fields_match:
        mismatch_type = (
            f"{worker_action.action_type}_vs_{verifier_action.action_type}"
            if worker_action.action_type != verifier_action.action_type
            else "amount_or_target_mismatch"
        )
        notes = (
            f"Worker proposed {worker_action.action_type} (amount={worker_action.amount}, "
            f"target_transaction={worker_action.target_transaction_id!r}, "
            f"target_subscription={worker_action.target_subscription_id!r}). Independent verification reached a "
            f"different conclusion: {verifier_action.action_type} (amount={verifier_action.amount}, "
            f"target_transaction={verifier_action.target_transaction_id!r}, "
            f"target_subscription={verifier_action.target_subscription_id!r})."
        )
    elif low_confidence:
        mismatch_type = "low_confidence"
        notes = (
            f"Worker and verifier agree on {worker_action.action_type}, but the verifier's independent "
            f"confidence ({verifier_action.confidence:.2f}) is below the {_LOW_CONFIDENCE_THRESHOLD} "
            f"threshold for auto-execution. Routing to human review rather than executing on a shaky signal."
        )
    else:
        mismatch_type = None
        notes = f"Worker and verifier independently agree: {worker_action.action_type}."

    return VerificationResult(
        consistent=consistent,
        mismatch_type=mismatch_type,
        verifier_rationale=verifier_action.rationale,
        notes=notes,
        final_decision="execute" if consistent else "escalate",
    )
