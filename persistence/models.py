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

    # Stripe customer id, resolved later by the worker agent via customer_email.
    customer_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    customer_email: Mapped[str] = mapped_column(String, index=True)
    ticket_text: Mapped[str] = mapped_column(Text)

    # Lifecycle status, see docs/architecture.md section 5 for the full enum.
    status: Mapped[str] = mapped_column(String, default="new", index=True)

    classification: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stripe_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    proposed_action: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Set on the first Slack post for this ticket, later posts reply into the thread.
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

    # Verifier's own independently re-pulled Stripe/ticket data.
    raw_verification_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="verification_results")


class AuditRecord(Base):
    __tablename__ = "audit_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)

    # Append-only: one row per decision-chain event, not one blob per ticket.
    event_type: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str] = mapped_column(String)  # worker_agent / opa / verifier_agent / stripe_executor / human
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="audit_records")
