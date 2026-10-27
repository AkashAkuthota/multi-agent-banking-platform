"""Authenticated MCP tools that prepare (but never apply) write commands."""

from decimal import Decimal
from typing import TYPE_CHECKING

from app.core.database import async_session
from app.mcp.context import get_authenticated_customer_id
from app.services.banking_command_service import (
    propose_internal_transfer as propose_transfer,
    propose_service_request as propose_request,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


async def propose_internal_transfer(
    source_account_id: str,
    destination_account_id: str,
    amount: Decimal,
    idempotency_key: str,
) -> dict[str, str | bool | None]:
    """Prepare an own-account transfer; confirmation is a separate action."""
    customer_id = get_authenticated_customer_id()
    async with async_session() as session:
        return await propose_transfer(
            session,
            customer_id,
            source_account_id,
            destination_account_id,
            amount,
            idempotency_key,
        )


async def create_service_request(
    request_type: str,
    idempotency_key: str,
) -> dict[str, str | bool | None]:
    """Prepare a service request; it is created only after separate confirmation."""
    customer_id = get_authenticated_customer_id()
    async with async_session() as session:
        return await propose_request(
            session,
            customer_id,
            request_type,
            idempotency_key,
        )


def register_banking_command_tools(server: "FastMCP") -> None:
    server.tool(
        name="propose_internal_transfer",
        description=(
            "Prepare a transfer between two accounts owned by the authenticated "
            "customer. This never moves funds. A later explicit chat confirmation "
            "with the proposal ID or the authenticated HTTP confirmation endpoint is required."
        ),
    )(propose_internal_transfer)
    server.tool(
        name="create_service_request",
        description=(
            "Prepare an allow-listed service request. This does not create the request "
            "until the authenticated customer explicitly confirms it in a later chat "
            "turn with the proposal ID or through the HTTP confirmation endpoint."
        ),
    )(create_service_request)
