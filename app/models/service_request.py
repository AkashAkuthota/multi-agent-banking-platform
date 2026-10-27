"""Service request model mapped to the existing service_requests table."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ServiceRequest(Base):
    """A service request belonging to a customer."""

    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("nextval('service_requests_id_seq'::regclass)"),
    )
    request_id: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("customers.id"),
        nullable=False,
    )
    request_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, object] | None] = mapped_column(
        "metadata",
        JSONB,
    )
