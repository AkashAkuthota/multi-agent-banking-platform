"""Account operations with customer ownership enforced in SQL."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account


async def get_customer_accounts(
    session: AsyncSession,
    customer_id: int,
) -> list[Account]:
    result = await session.scalars(
        select(Account).where(Account.customer_id == customer_id)
    )
    return list(result.all())


async def get_customer_account(
    session: AsyncSession,
    customer_id: int,
    account_id: str,
) -> Account | None:
    return await session.scalar(
        select(Account).where(
            Account.account_id == account_id,
            Account.customer_id == customer_id,
        )
    )
