"""Durable transfer and service-request command records."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Uuid,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


_WORKFLOW_STATUSES = "('PENDING', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'CONFLICTED')"
_AUDIT_EVENTS = "('PROPOSED', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'CONFLICTED')"


class TransferRequest(Base):
    """A pending internal transfer proposal."""

    __tablename__ = "transfer_requests"
    __table_args__ = (
        CheckConstraint(
            "amount > 0 AND amount <= 50000.00",
            name="ck_transfer_requests_amount_range",
        ),
        CheckConstraint(
            "source_account_id <> destination_account_id",
            name="ck_transfer_requests_distinct_accounts",
        ),
        CheckConstraint(
            f"status IN {_WORKFLOW_STATUSES}",
            name="ck_transfer_requests_status",
        ),
        UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_transfer_requests_customer_idempotency",
        ),
        Index(
            "uq_transfer_requests_pending_source",
            "source_account_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_transfer_requests_customer_created",
            "customer_id",
            text("created_at DESC"),
        ),
    )

    request_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id"),
        nullable=False,
    )
    source_account_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    source_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id"),
        nullable=False,
    )
    destination_account_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    destination_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_transaction_id: Mapped[str | None] = mapped_column(String(30), unique=True)
    destination_transaction_id: Mapped[str | None] = mapped_column(
        String(30),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TransferAuditEvent(Base):
    """Audit event for a transfer proposal state transition."""

    __tablename__ = "transfer_audit_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN {_AUDIT_EVENTS}",
            name="ck_transfer_audit_events_type",
        ),
        Index("ix_transfer_audit_request_time", "request_id", "occurred_at"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("transfer_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_account_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    destination_account_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class ServiceRequestProposal(Base):
    """A pending proposal to open an allow-listed service request."""

    __tablename__ = "service_request_proposals"
    __table_args__ = (
        CheckConstraint(
            "request_type IN ('STATEMENT', 'CHEQUE_BOOK', 'CARD_REPLACEMENT')",
            name="ck_service_request_proposals_type",
        ),
        CheckConstraint(
            f"status IN {_WORKFLOW_STATUSES}",
            name="ck_service_request_proposals_status",
        ),
        UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_service_request_proposals_customer_idempotency",
        ),
        Index(
            "ix_service_request_proposals_customer_created",
            "customer_id",
            text("created_at DESC"),
        ),
    )

    request_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id"),
        nullable=False,
    )
    request_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_service_request_id: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("service_requests.request_id"),
        unique=True,
    )


class ServiceRequestProposalAuditEvent(Base):
    """Audit event for a proposed service request transition."""

    __tablename__ = "service_request_proposal_audit_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN {_AUDIT_EVENTS}",
            name="ck_service_request_proposal_audit_events_type",
        ),
        Index(
            "ix_service_request_proposal_audit_request_time",
            "request_id",
            "occurred_at",
        ),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("service_request_proposals.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    request_type: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
