"""Tests for the Transactions MCP tool."""

import inspect
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.mcp.context import (
    AuthenticatedExecutionContext,
    authenticated_customer_context,
)
from app.mcp.server import mcp_server
from app.mcp.transactions import get_my_transactions
from app.models import Account, Transaction


class TransactionsMCPTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_transaction() -> Transaction:
        return Transaction(
            id=9,
            transaction_id="TXN_009",
            account_id=7,
            transaction_type="DEBIT",
            amount=Decimal("125.00"),
            merchant="Example Store",
            category="Shopping",
            transaction_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
            status="COMPLETED",
            description="Card purchase",
        )

    async def test_tool_is_registered_with_bounded_pagination_schema(self) -> None:
        tools = await mcp_server.list_tools()
        transaction_tools = [tool for tool in tools if tool.name == "get_my_transactions"]

        self.assertEqual(len(transaction_tools), 1)
        self.assertEqual(
            set(transaction_tools[0].inputSchema["properties"]),
            {"limit", "offset"},
        )
        self.assertEqual(
            list(inspect.signature(get_my_transactions).parameters),
            ["limit", "offset"],
        )

    async def test_tool_uses_context_customer_and_transaction_service(self) -> None:
        transaction = self.make_transaction()
        account = Account(id=7, currency="INR")
        session = AsyncMock()
        with patch("app.mcp.transactions.async_session") as session_factory:
            with patch(
                "app.mcp.transactions.get_customer_transactions",
                new=AsyncMock(return_value=[transaction]),
            ) as get_transactions:
                with patch(
                    "app.mcp.transactions.get_customer_accounts",
                    new=AsyncMock(return_value=[account]),
                ) as get_accounts:
                    session_factory.return_value.__aenter__.return_value = session
                    with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                        result = await get_my_transactions()

        get_transactions.assert_awaited_once_with(session, 42, limit=20, offset=0)
        get_accounts.assert_awaited_once_with(session, 42)
        self.assertEqual(
            result,
            [
                {
                    "transaction_id": "TXN_009",
                    "transaction_type": "DEBIT",
                    "amount": "125.00",
                    "currency": "INR",
                    "merchant": "Example Store",
                    "category": "Shopping",
                    "transaction_date": "2026-01-02T00:00:00+00:00",
                    "status": "COMPLETED",
                    "description": "Card purchase",
                }
            ],
        )

    async def test_tool_fails_closed_without_trusted_identity(self) -> None:
        with self.assertRaises(PermissionError):
            await get_my_transactions()

    async def test_mcp_runtime_dispatch_uses_bound_customer_identity(self) -> None:
        session = AsyncMock()
        with patch("app.mcp.transactions.async_session") as session_factory:
            with patch(
                "app.mcp.transactions.get_customer_transactions",
                new=AsyncMock(return_value=[]),
            ) as get_transactions:
                with patch(
                    "app.mcp.transactions.get_customer_accounts",
                    new=AsyncMock(return_value=[]),
                ) as get_accounts:
                    session_factory.return_value.__aenter__.return_value = session
                    with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                        await mcp_server.call_tool("get_my_transactions", {})

        get_transactions.assert_awaited_once_with(session, 42, limit=20, offset=0)
        get_accounts.assert_awaited_once_with(session, 42)

    async def test_supplied_customer_id_cannot_override_trusted_identity(self) -> None:
        session = AsyncMock()
        with patch("app.mcp.transactions.async_session") as session_factory:
            with patch(
                "app.mcp.transactions.get_customer_transactions",
                new=AsyncMock(return_value=[]),
            ) as get_transactions:
                with patch(
                    "app.mcp.transactions.get_customer_accounts",
                    new=AsyncMock(return_value=[]),
                ) as get_accounts:
                    session_factory.return_value.__aenter__.return_value = session
                    with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                        result = await mcp_server.call_tool(
                            "get_my_transactions",
                            {"customer_id": 99},
                        )

        self.assertEqual(result[1], {"result": []})
        get_transactions.assert_awaited_once_with(session, 42, limit=20, offset=0)
        get_accounts.assert_awaited_once_with(session, 42)


if __name__ == "__main__":
    unittest.main()
