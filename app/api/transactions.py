"""Transaction endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_customer
from app.core.database import get_db
from app.models import Transaction
from app.services.transaction_service import (
    get_customer_transaction,
    get_customer_transaction_page,
)
from app.services.transaction_service import (
    DEFAULT_TRANSACTION_PAGE_SIZE,
    MAX_TRANSACTION_PAGE_SIZE,
)

router = APIRouter(prefix="/transactions", tags=["Transactions"])


def _transaction_response(transaction: Transaction) -> dict[str, object]:
    return {
        "transaction_id": transaction.transaction_id,
        "transaction_type": transaction.transaction_type,
        "amount": transaction.amount,
        "merchant": transaction.merchant,
        "category": transaction.category,
        "transaction_date": transaction.transaction_date,
        "status": transaction.status,
        "description": transaction.description,
    }


@router.get("/me")
async def get_my_transactions(
    customer_id: Annotated[int, Depends(get_current_customer)],
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_TRANSACTION_PAGE_SIZE),
    ] = DEFAULT_TRANSACTION_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    page = await get_customer_transaction_page(
        session,
        customer_id,
        limit=limit,
        offset=offset,
    )
    return {
        "transactions": [
            _transaction_response(transaction) for transaction in page.transactions
        ],
        "limit": limit,
        "offset": offset,
        "next_offset": page.next_offset,
    }


@router.get("/{transaction_id}")
async def get_transaction(
    transaction_id: str,
    customer_id: Annotated[int, Depends(get_current_customer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    transaction = await get_customer_transaction(
        session,
        customer_id,
        transaction_id,
    )
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found",
        )

    return _transaction_response(transaction)
