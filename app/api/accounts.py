"""Account endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_customer
from app.core.database import get_db
from app.core.pii import account_number_last4
from app.models import Account
from app.services.account_service import (
    get_customer_account,
    get_customer_accounts,
)

router = APIRouter(prefix="/accounts", tags=["Accounts"])


def _account_response(account: Account) -> dict[str, object]:
    return {
        "account_id": account.account_id,
        "account_type": account.account_type,
        "account_number_last4": account_number_last4(account.account_number),
        "balance": account.balance,
        "currency": account.currency,
        "status": account.status,
        "created_at": account.created_at,
    }


@router.get("/me")
async def get_my_accounts(
    customer_id: Annotated[int, Depends(get_current_customer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, list[dict[str, object]]]:
    accounts = await get_customer_accounts(session, customer_id)
    if not accounts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No accounts found",
        )

    return {
        "accounts": [_account_response(account) for account in accounts]
    }


@router.get("/{account_id}")
async def get_account(
    account_id: str,
    customer_id: Annotated[int, Depends(get_current_customer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    account = await get_customer_account(session, customer_id, account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    return _account_response(account)
