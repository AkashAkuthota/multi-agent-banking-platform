"""Sandbox daily-limit records."""

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


class DailyAccountLimit(Base):
    """Configured daily limit for one account."""

    __tablename__ = "daily_account_limits"
    __table_args__ = (
        CheckConstraint(
            "daily_limit >= 10000.00 AND daily_limit <= 50000.00",
            name="ck_daily_account_limits_policy_range",
        ),
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class DailyLimitChangeRequest(Base):
    """Durable proposal and outcome for a daily-limit change."""

    __tablename__ = "daily_limit_change_requests"
    __table_args__ = (
        CheckConstraint(
            "requested_limit >= 10000.00 AND requested_limit <= 50000.00",
            name="ck_daily_limit_requests_policy_range",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'CONFLICTED')",
            name="ck_daily_limit_requests_status",
        ),
        UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_daily_limit_requests_customer_idempotency",
        ),
        Index(
            "uq_daily_limit_requests_pending_account",
            "account_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_daily_limit_requests_customer_created",
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
    account_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id"),
        nullable=False,
    )
    previous_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    requested_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyLimitAuditEvent(Base):
    """Append-only audit record for each workflow transition."""

    __tablename__ = "daily_limit_audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('PROPOSED', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'CONFLICTED')",
            name="ck_daily_limit_audit_events_type",
        ),
        Index(
            "ix_daily_limit_audit_request_time",
            "request_id",
            "occurred_at",
        ),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("daily_limit_change_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    previous_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    requested_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
