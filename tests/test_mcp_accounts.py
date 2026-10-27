"""Tests for the Accounts MCP tool and server registration."""

import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.mcp.accounts import get_my_accounts
from app.mcp.context import (
    AuthenticatedExecutionContext,
    authenticated_customer_context,
    get_authenticated_customer_id,
)
from app.mcp.server import mcp_server
from app.models import Account


class AccountsMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_uses_context_identity_and_account_service(self) -> None:
        account = Account(
            id=5,
            account_id="ACC_005",
            customer_id=42,
            account_type="SAVINGS",
            account_number="5550012345",
            balance=Decimal("200.50"),
            currency="INR",
            status="ACTIVE",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session = AsyncMock()
        session_factory = patch("app.mcp.accounts.async_session")
        service = patch(
            "app.mcp.accounts.get_customer_accounts",
            new=AsyncMock(return_value=[account]),
        )
        with ExitStack() as stack:
            factory = stack.enter_context(session_factory)
            get_accounts = stack.enter_context(service)
            factory.return_value.__aenter__.return_value = session
            with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                result = await get_my_accounts()

        get_accounts.assert_awaited_once_with(session, 42)
        self.assertEqual(
            result,
            [
                {
                    "account_id": "ACC_005",
                    "account_type": "SAVINGS",
                    "account_number_last4": "2345",
                    "balance": "200.50",
                    "currency": "INR",
                    "status": "ACTIVE",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        )

    async def test_tool_fails_closed_without_authenticated_context(self) -> None:
        with self.assertRaises(PermissionError):
            await get_my_accounts()

    async def test_tool_schema_has_no_customer_id_argument(self) -> None:
        tools = await mcp_server.list_tools()
        account_tool = next(tool for tool in tools if tool.name == "get_my_accounts")

        self.assertIn("get_my_accounts", [tool.name for tool in tools])
        self.assertEqual(account_tool.inputSchema["properties"], {})
        self.assertNotIn("customer_id", account_tool.inputSchema["properties"])

    async def test_server_dispatch_uses_bound_customer_context(self) -> None:
        session = AsyncMock()
        with patch("app.mcp.accounts.async_session") as session_factory:
            with patch(
                "app.mcp.accounts.get_customer_accounts",
                new=AsyncMock(return_value=[]),
            ) as get_accounts:
                session_factory.return_value.__aenter__.return_value = session
                with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                    await mcp_server.call_tool("get_my_accounts", {})

        get_accounts.assert_awaited_once_with(session, 42)

    def test_customer_context_does_not_leak_after_scope(self) -> None:
        with authenticated_customer_context(AuthenticatedExecutionContext(42)):
            self.assertEqual(get_authenticated_customer_id(), 42)

        with self.assertRaises(PermissionError):
            get_authenticated_customer_id()


if __name__ == "__main__":
    unittest.main()
