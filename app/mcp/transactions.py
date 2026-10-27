"""Transactions MCP tools."""

from typing import TYPE_CHECKING

from app.core.database import async_session
from app.mcp.context import get_authenticated_customer_id
from app.services.account_service import get_customer_accounts
from app.services.transaction_service import get_customer_transactions

DEFAULT_CHAT_TRANSACTION_PAGE_SIZE = 20

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


async def get_my_transactions(
    limit: int = DEFAULT_CHAT_TRANSACTION_PAGE_SIZE,
    offset: int = 0,
) -> list[dict[str, str | None]]:
    """Return one bounded page of the customer's transactions."""
    customer_id = get_authenticated_customer_id()
    async with async_session() as session:
        transactions = await get_customer_transactions(
            session,
            customer_id,
            limit=limit,
            offset=offset,
        )
        accounts = await get_customer_accounts(session, customer_id)

    currencies_by_account_id = {
        account.id: account.currency for account in accounts
    }

    return [
        {
            "transaction_id": transaction.transaction_id,
            "transaction_type": transaction.transaction_type,
            "amount": str(transaction.amount),
            "currency": currencies_by_account_id[transaction.account_id],
            "merchant": transaction.merchant,
            "category": transaction.category,
            "transaction_date": transaction.transaction_date.isoformat(),
            "status": transaction.status,
            "description": transaction.description,
        }
        for transaction in transactions
    ]


def register_transaction_tools(server: "FastMCP") -> None:
    server.tool(
        name="get_my_transactions",
        description=(
            "Get one bounded page of transactions belonging to the authenticated "
            "customer's accounts. Defaults to the most recent 20 transactions."
        ),
    )(get_my_transactions)
