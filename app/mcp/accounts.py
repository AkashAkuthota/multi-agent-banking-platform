"""Accounts MCP tools."""

from typing import TYPE_CHECKING

from app.core.database import async_session
from app.core.pii import account_number_last4
from app.mcp.context import get_authenticated_customer_id
from app.services.account_service import get_customer_accounts

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


async def get_my_accounts() -> list[dict[str, str | None]]:
    """Return accounts owned by the authenticated customer."""
    customer_id = get_authenticated_customer_id()
    async with async_session() as session:
        accounts = await get_customer_accounts(session, customer_id)

    return [
        {
            "account_id": account.account_id,
            "account_type": account.account_type,
            "account_number_last4": account_number_last4(account.account_number),
            "balance": str(account.balance),
            "currency": account.currency,
            "status": account.status,
            "created_at": account.created_at.isoformat(),
        }
        for account in accounts
    ]


def register_account_tools(server: "FastMCP") -> None:
    server.tool(
        name="get_my_accounts",
        description="Get accounts belonging to the authenticated customer.",
    )(get_my_accounts)
