"""Accounts-domain orchestration over the existing MCP operation."""

from app.mcp.context import AuthenticatedExecutionContext
from app.mcp.server import MCPToolInvoker, call_authenticated_mcp_tool


class AccountsAgent:
    """Invoke the authenticated-customer accounts MCP tool."""

    def __init__(
        self,
        tool_invoker: MCPToolInvoker = call_authenticated_mcp_tool,
    ) -> None:
        self._tool_invoker = tool_invoker

    async def execute(
        self,
        execution_context: AuthenticatedExecutionContext,
    ) -> dict[str, object]:
        return await self._tool_invoker(
            execution_context,
            "get_my_accounts",
        )
