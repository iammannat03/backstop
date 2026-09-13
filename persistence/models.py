import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.db import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zendesk_ticket_id: Mapped[str] = mapped_column(String, unique=True, index=True)

    # customer_id is the Stripe customer id (cus_...), unknown at ingestion time, resolved
    # and filled in by the worker agent's Stripe Investigator (phase 4) via customer_email
    # lookup. customer_email is what ingestion actually has from the Zendesk requester.
    customer_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    customer_email: Mapped[str] = mapped_column(String, index=True)
    ticket_text: Mapped[str] = mapped_column(Text)

    # Lifecycle status, see docs/architecture.md section 5 (state diagram) for the full enum:
    # new, investigating, proposed_action, opa_review, blocked, verifier_review,
    # escalated, executing, executed, human_review, resolved
    status: Mapped[str] = mapped_column(String, default="new", index=True)

    classification: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stripe_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    proposed_action: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Set the first time any audit Slack message is posted for this ticket
    # (audit/slack_notifier.py); every later post for the same ticket replies
    # into this thread instead of starting a new top-level message, which is
    # what lets a human reply "@backstop <action>" in-thread and have
    # audit/slack_commands.py resolve it back to this ticket.
    slack_channel: Mapped[str | None] = mapped_column(String, nullable=True)
    slack_thread_ts: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    policy_decisions: Mapped[list["PolicyDecision"]] = relationship(back_populates="ticket")
    verification_results: Mapped[list["VerificationResult"]] = relationship(back_populates="ticket")
    audit_records: Mapped[list["AuditRecord"]] = relationship(back_populates="ticket")


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)

    decision: Mapped[str] = mapped_column(String)  # allow / deny / escalate
    matched_rule: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # ProposedAction submitted to OPA
    raw_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # full OPA response

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="policy_decisions")


class VerificationResult(Base):
    __tablename__ = "verification_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)

    consistent: Mapped[bool] = mapped_column(Boolean)
    mismatch_type: Mapped[str | None] = mapped_column(String, nullable=True)
    verifier_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_decision: Mapped[str | None] = mapped_column(String, nullable=True)  # execute / escalate

    # Verifier's own independently re-pulled Stripe/ticket data, kept for audit;
    # proves the re-derivation actually happened rather than trusting the worker's summary
    raw_verification_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="verification_results")


class AuditRecord(Base):
    __tablename__ = "audit_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)

    # One row per decision-chain event (e.g. proposed_action, policy_decision,
    # verification_result, executed, escalated) rather than one growing blob per ticket,
    # see docs/rules.md for why.
    event_type: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str] = mapped_column(String)  # worker_agent / opa / verifier_agent / stripe_executor / human
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="audit_records")
