"""Authenticated MCP tools for the sandbox daily-limit workflow."""

from decimal import Decimal
from typing import TYPE_CHECKING

from app.core.database import async_session
from app.mcp.context import get_authenticated_customer_id
from app.services.daily_limit_service import (
    get_customer_daily_limit,
    propose_daily_limit_change as propose_change,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


async def get_my_daily_limit(account_id: str) -> dict[str, str]:
    """Get the daily limit for an account owned by the authenticated customer."""
    customer_id = get_authenticated_customer_id()
    async with async_session() as session:
        return await get_customer_daily_limit(session, customer_id, account_id)


async def propose_daily_limit_change(
    account_id: str,
    requested_limit: Decimal,
    idempotency_key: str,
) -> dict[str, str | bool]:
    """Prepare a sandbox daily-limit change; this does not apply the new limit."""
    customer_id = get_authenticated_customer_id()
    async with async_session() as session:
        return await propose_change(
            session,
            customer_id,
            account_id,
            requested_limit,
            idempotency_key,
        )


def register_daily_limit_tools(server: "FastMCP") -> None:
    server.tool(
        name="get_my_daily_limit",
        description="Get the sandbox daily limit for an owned account.",
    )(get_my_daily_limit)
    server.tool(
        name="propose_daily_limit_change",
        description=(
            "Create a pending sandbox daily-limit change. This never applies the "
            "change; a later explicit chat confirmation with the proposal ID or "
            "the authenticated HTTP confirmation endpoint is required."
        ),
    )(propose_daily_limit_change)
