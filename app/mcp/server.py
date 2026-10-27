"""MCP server for banking tools."""

from collections.abc import Awaitable, Mapping
from typing import Literal, Protocol

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from app.mcp.accounts import register_account_tools
from app.mcp.banking_commands import register_banking_command_tools
from app.mcp.context import (
    AuthenticatedExecutionContext,
    authenticated_customer_context,
)
from app.mcp.daily_limits import register_daily_limit_tools
from app.mcp.service_requests import register_service_request_tools
from app.mcp.transactions import register_transaction_tools
from app.policies import PolicyViolationError
from app.services.daily_limit_service import DailyLimitError

MCPToolName = Literal[
    "get_my_accounts",
    "get_my_transactions",
    "get_my_service_requests",
    "get_my_daily_limit",
    "propose_daily_limit_change",
    "propose_internal_transfer",
    "create_service_request",
]


class MCPToolInvoker(Protocol):
    """Callable MCP boundary that supports optional typed tool arguments."""

    def __call__(
        self,
        execution_context: AuthenticatedExecutionContext,
        tool_name: MCPToolName,
        arguments: Mapping[str, object] | None = None,
    ) -> Awaitable[dict[str, object]]: ...


mcp_server = FastMCP(
    "banking-platform",
    instructions="Banking tools operate only on the authenticated customer's data.",
)
register_account_tools(mcp_server)
register_transaction_tools(mcp_server)
register_service_request_tools(mcp_server)
register_daily_limit_tools(mcp_server)
register_banking_command_tools(mcp_server)


async def call_authenticated_mcp_tool(
    execution_context: AuthenticatedExecutionContext,
    tool_name: MCPToolName,
    arguments: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Dispatch an MCP tool under a trusted customer context."""
    try:
        with authenticated_customer_context(execution_context):
            _, structured_content = await mcp_server.call_tool(
                tool_name,
                dict(arguments or {}),
            )
    except ToolError as error:
        if isinstance(
            error.__cause__,
            (DailyLimitError, PolicyViolationError),
        ):
            raise error.__cause__ from error
        raise

    if not isinstance(structured_content, dict):
        raise RuntimeError("MCP tool returned no structured content")
    return structured_content


if __name__ == "__main__":
    mcp_server.run(transport="stdio")
