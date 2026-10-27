"""Transaction model mapped to the existing transactions table."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Transaction(Base):
    """A transaction belonging to an account."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(30), nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id"),
        nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    merchant: Mapped[str | None] = mapped_column(String(150))
    category: Mapped[str | None] = mapped_column(String(50))
    transaction_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
