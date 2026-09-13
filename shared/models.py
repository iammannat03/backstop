"""Pydantic schemas shared across worker_agent and verifier_agent."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Keep in sync with shared/action_types.py's ACTION_TYPE_VALUES.
ActionType = Literal[
    "refund",
    "partial_refund",
    "no_action",
    "escalate",
    "cancel_subscription",
    "apply_account_credit",
    "flag_for_fraud_review",
]


class Classification(BaseModel):
    """Ticket Classifier output."""

    is_billing_relevant: bool
    intent: Literal[
        "refund_request",
        "duplicate_charge",
        "billing_inquiry",
        "cancellation_request",
        "subscription_change",
        "other",
    ]
    urgency: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)


class Transaction(BaseModel):
    """One Stripe charge, with proration context folded in."""

    id: str
    amount: int  # cents
    currency: str
    description: str | None = None
    created: datetime
    refunded: bool
    amount_refunded: int
    disputed: bool
    invoice_id: str | None = None
    is_proration: bool = False
    proration_details: str | None = None


class Subscription(BaseModel):
    id: str
    status: str
    plan_nickname: str | None = None
    current_period_start: datetime
    current_period_end: datetime


class StripeHistory(BaseModel):
    """Stripe Investigator output."""

    customer_id: str | None = None  # None if no Stripe customer matched the ticket's email
    customer_metadata: dict = Field(default_factory=dict)
    transactions: list[Transaction] = Field(default_factory=list)
    subscriptions: list[Subscription] = Field(default_factory=list)
    refunds_last_30_days: int = 0


class ProposedAction(BaseModel):
    """Action Proposer output."""

    action_type: ActionType
    amount: int = 0  # cents; 0 for no_action/escalate/cancel_subscription/flag_for_fraud_review
    currency: str = "usd"
    target_transaction_id: str | None = None
    # Only meaningful for cancel_subscription.
    target_subscription_id: str | None = None
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class VerificationResult(BaseModel):
    """Consistency Checker output."""

    consistent: bool
    mismatch_type: str | None = None
    verifier_rationale: str
    notes: str
    final_decision: Literal["execute", "escalate"]
