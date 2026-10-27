"""Tests for the Service Requests MCP tool."""

import inspect
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from app.mcp.context import (
    AuthenticatedExecutionContext,
    authenticated_customer_context,
)
from app.mcp.server import mcp_server
from app.mcp.service_requests import get_my_service_requests
from app.models import ServiceRequest


class ServiceRequestsMCPTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_service_request() -> ServiceRequest:
        return ServiceRequest(
            id=3,
            request_id="SR_003",
            customer_id=42,
            request_type="STATEMENT",
            status="COMPLETED",
            created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            completed_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        )

    async def test_tool_is_registered_with_zero_model_facing_arguments(self) -> None:
        tools = await mcp_server.list_tools()
        service_request_tools = [
            tool for tool in tools if tool.name == "get_my_service_requests"
        ]

        self.assertEqual(len(service_request_tools), 1)
        self.assertEqual(service_request_tools[0].inputSchema["properties"], {})
        self.assertEqual(inspect.signature(get_my_service_requests).parameters, {})

    async def test_tool_uses_context_identity_and_service(self) -> None:
        service_request = self.make_service_request()
        session = AsyncMock()
        with patch("app.mcp.service_requests.async_session") as session_factory:
            with patch(
                "app.mcp.service_requests.get_customer_service_requests",
                new=AsyncMock(return_value=[service_request]),
            ) as get_requests:
                session_factory.return_value.__aenter__.return_value = session
                with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                    result = await get_my_service_requests()

        get_requests.assert_awaited_once_with(session, 42)
        self.assertEqual(
            result,
            [
                {
                    "request_id": "SR_003",
                    "request_type": "STATEMENT",
                    "status": "COMPLETED",
                    "created_at": "2026-01-03T00:00:00+00:00",
                    "completed_at": "2026-01-04T00:00:00+00:00",
                }
            ],
        )

    async def test_tool_fails_closed_without_trusted_identity(self) -> None:
        with self.assertRaises(PermissionError):
            await get_my_service_requests()

    async def test_runtime_dispatch_uses_bound_customer_identity(self) -> None:
        session = AsyncMock()
        with patch("app.mcp.service_requests.async_session") as session_factory:
            with patch(
                "app.mcp.service_requests.get_customer_service_requests",
                new=AsyncMock(return_value=[]),
            ) as get_requests:
                session_factory.return_value.__aenter__.return_value = session
                with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                    await mcp_server.call_tool("get_my_service_requests", {})

        get_requests.assert_awaited_once_with(session, 42)

    async def test_supplied_customer_id_cannot_override_trusted_context(self) -> None:
        session = AsyncMock()
        with patch("app.mcp.service_requests.async_session") as session_factory:
            with patch(
                "app.mcp.service_requests.get_customer_service_requests",
                new=AsyncMock(return_value=[]),
            ) as get_requests:
                session_factory.return_value.__aenter__.return_value = session
                with authenticated_customer_context(AuthenticatedExecutionContext(42)):
                    await mcp_server.call_tool(
                        "get_my_service_requests",
                        {"customer_id": 99},
                    )

        get_requests.assert_awaited_once_with(session, 42)

    async def test_service_layer_still_enforces_customer_ownership(self) -> None:
        from app.services.service_request_service import (
            get_customer_service_requests,
        )

        session = AsyncMock()
        result = Mock()
        result.all.return_value = []
        session.scalars.return_value = result

        await get_customer_service_requests(session, 42)

        statement = session.scalars.call_args.args[0]
        self.assertIn(
            "service_requests.customer_id",
            str(statement.compile()),
        )
        self.assertEqual(statement.compile().params["customer_id_1"], 42)


if __name__ == "__main__":
    unittest.main()
