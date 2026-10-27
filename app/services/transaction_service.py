"""Transaction operations with account ownership enforced in SQL."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Transaction

DEFAULT_TRANSACTION_PAGE_SIZE = 25
MAX_TRANSACTION_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class TransactionPage:
    """A bounded customer-owned transaction page."""

    transactions: list[Transaction]
    next_offset: int | None


async def get_customer_transactions(
    session: AsyncSession,
    customer_id: int,
    *,
    limit: int = DEFAULT_TRANSACTION_PAGE_SIZE,
    offset: int = 0,
) -> list[Transaction]:
    page = await get_customer_transaction_page(
        session,
        customer_id,
        limit=limit,
        offset=offset,
    )
    return page.transactions


async def get_customer_transaction_page(
    session: AsyncSession,
    customer_id: int,
    *,
    limit: int = DEFAULT_TRANSACTION_PAGE_SIZE,
    offset: int = 0,
) -> TransactionPage:
    """Get one owned transaction page, reading one extra row for pagination."""
    if not 1 <= limit <= MAX_TRANSACTION_PAGE_SIZE:
        raise ValueError("Transaction page size is outside the allowed range")
    if offset < 0:
        raise ValueError("Transaction offset cannot be negative")
    result = await session.scalars(
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.customer_id == customer_id)
        .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .limit(limit + 1)
        .offset(offset)
    )
    transactions = list(result.all())
    has_more = len(transactions) > limit
    page_transactions = transactions[:limit]
    return TransactionPage(
        transactions=page_transactions,
        next_offset=offset + limit if has_more else None,
    )


async def get_customer_transaction(
    session: AsyncSession,
    customer_id: int,
    transaction_id: str,
) -> Transaction | None:
    return await session.scalar(
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.transaction_id == transaction_id,
            Account.customer_id == customer_id,
        )
    )
