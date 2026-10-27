"""Tests for authenticated FastAPI-to-MCP execution."""

import asyncio
import unittest
from decimal import Decimal
from uuid import UUID
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.jwt import create_access_token
from app.main import app
from app.mcp.context import (
    AuthenticatedExecutionContext,
    get_authenticated_customer_id,
)
from app.mcp.server import call_authenticated_mcp_tool
from app.services.daily_limit_service import DailyLimitPolicyError
from app.services.transaction_service import TransactionPage


class MCPAuthBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = (
            patch.object(settings, "JWT_SECRET_KEY", "test-secret-key-for-auth-tests-long-enough"),
            patch.object(settings, "JWT_ALGORITHM", "HS256"),
            patch.object(settings, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 5),
        )
        for setting_patch in self.settings:
            setting_patch.start()
            self.addCleanup(setting_patch.stop)

    async def test_http_bridge_uses_customer_from_verified_bearer_token(self) -> None:
        session = AsyncMock()
        with patch("app.mcp.accounts.async_session") as session_factory:
            with patch(
                "app.mcp.accounts.get_customer_accounts",
                new=AsyncMock(return_value=[]),
            ) as get_accounts:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp/tools/get_my_accounts",
                        headers={
                            "Authorization": f"Bearer {create_access_token(42)}",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"result": []})
        get_accounts.assert_awaited_once_with(session, 42)

    async def test_http_bridge_rejects_missing_authentication(self) -> None:
        with patch(
            "app.mcp.accounts.get_customer_accounts",
            new=AsyncMock(),
        ) as get_accounts:
            with TestClient(app) as client:
                response = client.post("/mcp/tools/get_my_accounts")

        self.assertEqual(response.status_code, 401)
        get_accounts.assert_not_awaited()

    async def test_http_bridge_rejects_tool_arguments_and_customer_override(self) -> None:
        with patch(
            "app.mcp.accounts.get_customer_accounts",
            new=AsyncMock(),
        ) as get_accounts:
            with TestClient(app) as client:
                response = client.post(
                    "/mcp/tools/get_my_accounts",
                    headers={
                        "Authorization": f"Bearer {create_access_token(42)}",
                    },
                    json={"customer_id": 99},
                )
                query_response = client.post(
                    "/mcp/tools/get_my_accounts",
                    headers={
                        "Authorization": f"Bearer {create_access_token(42)}",
                    },
                    params={"customer_id": 99},
                )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(query_response.status_code, 422)
        get_accounts.assert_not_awaited()

    async def test_model_facing_bridge_can_only_propose_daily_limit_change(self) -> None:
        session = AsyncMock()
        token = create_access_token(42)
        proposal = {
            "request_id": "d72132e2-cf94-44b2-a4b2-64ad82d07f71",
            "account_id": "ACC_005",
            "previous_limit": "10000.00",
            "requested_limit": "20000.00",
            "currency": "INR",
            "status": "PENDING",
            "expires_at": "2026-01-01T00:10:00+00:00",
            "applied": False,
        }
        with patch("app.mcp.daily_limits.async_session") as session_factory:
            with patch(
                "app.mcp.daily_limits.propose_change",
                new=AsyncMock(return_value=proposal),
            ) as propose:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp/tools/propose_daily_limit_change",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "account_id": "ACC_005",
                            "requested_limit": "20000.00",
                            "idempotency_key": "portfolio-demo-001",
                        },
                    )
                    rejected = client.post(
                        "/mcp/tools/propose_daily_limit_change",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "account_id": "ACC_005",
                            "requested_limit": "20000.00",
                            "idempotency_key": "portfolio-demo-001",
                            "customer_id": 99,
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rejected.status_code, 422)
        propose.assert_awaited_once_with(
            session,
            42,
            "ACC_005",
            Decimal("20000.00"),
            "portfolio-demo-001",
        )

    async def test_confirmation_is_a_separate_authenticated_action(self) -> None:
        session = AsyncMock()
        token = create_access_token(42)
        request_id = UUID("d72132e2-cf94-44b2-a4b2-64ad82d07f71")
        result = {"request_id": str(request_id), "status": "CONFIRMED", "applied": True}
        with patch("app.api.mcp.async_session") as session_factory:
            with patch(
                "app.api.mcp.confirm_daily_limit_change",
                new=AsyncMock(return_value=result),
            ) as confirm:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp/tools/confirm_daily_limit_change",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"request_id": str(request_id), "confirmed": True},
                    )
                    rejected = client.post(
                        "/mcp/tools/confirm_daily_limit_change",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "request_id": str(request_id),
                            "confirmed": False,
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rejected.status_code, 422)
        confirm.assert_awaited_once_with(session, 42, request_id, True)

    async def test_mcp_policy_failures_are_reported_as_validation_errors(self) -> None:
        token = create_access_token(42)
        with patch(
            "app.mcp.daily_limits.propose_change",
            new=AsyncMock(side_effect=DailyLimitPolicyError("outside policy")),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/mcp/tools/propose_daily_limit_change",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "account_id": "ACC_005",
                        "requested_limit": "9000",
                        "idempotency_key": "portfolio-demo-002",
                    },
                )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "outside policy")

    async def test_confirmation_is_not_registered_as_an_llm_mcp_tool(self) -> None:
        from app.mcp.server import mcp_server

        tools = await mcp_server.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}

        self.assertIn("propose_daily_limit_change", tools_by_name)
        self.assertNotIn("confirm_daily_limit_change", tools_by_name)
        self.assertNotIn("cancel_daily_limit_change", tools_by_name)
        self.assertEqual(
            set(tools_by_name["propose_daily_limit_change"].inputSchema["properties"]),
            {"account_id", "requested_limit", "idempotency_key"},
        )
        self.assertNotIn(
            "customer_id",
            tools_by_name["propose_daily_limit_change"].inputSchema["properties"],
        )

    async def test_new_write_tools_only_create_proposals(self) -> None:
        from app.mcp.server import mcp_server

        tools = await mcp_server.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}

        self.assertEqual(
            {
                "get_my_accounts",
                "get_my_transactions",
                "get_my_service_requests",
                "get_my_daily_limit",
                "propose_daily_limit_change",
                "propose_internal_transfer",
                "create_service_request",
            },
            set(tools_by_name),
        )
        self.assertNotIn("customer_id", tools_by_name["propose_internal_transfer"].inputSchema["properties"])
        self.assertNotIn("customer_id", tools_by_name["create_service_request"].inputSchema["properties"])
        self.assertNotIn("confirm_internal_transfer", tools_by_name)
        self.assertNotIn("confirm_service_request", tools_by_name)

    async def test_openapi_exposes_named_typed_mcp_operations(self) -> None:
        from app.main import app

        paths = app.openapi()["paths"]
        expected = {
            "/mcp/tools/get_my_accounts",
            "/mcp/tools/get_my_transactions",
            "/mcp/tools/get_my_service_requests",
            "/mcp/tools/get_my_daily_limit",
            "/mcp/tools/propose_daily_limit_change",
            "/mcp/tools/propose_internal_transfer",
            "/mcp/tools/create_service_request",
            "/mcp/tools/confirm_internal_transfer",
            "/mcp/tools/cancel_internal_transfer",
            "/mcp/tools/confirm_service_request",
            "/mcp/tools/cancel_service_request",
            "/mcp/tools/confirm_daily_limit_change",
            "/mcp/tools/cancel_daily_limit_change",
        }
        self.assertTrue(expected.issubset(paths))
        self.assertNotIn(
            "requestBody",
            paths["/mcp/tools/get_my_accounts"]["post"],
        )
        transfer_schema = paths["/mcp/tools/propose_internal_transfer"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]
        component_name = transfer_schema["$ref"].rsplit("/", 1)[-1]
        properties = app.openapi()["components"]["schemas"][component_name][
            "properties"
        ]
        self.assertEqual(
            set(properties),
            {
                "source_account_id",
                "destination_account_id",
                "amount",
                "idempotency_key",
            },
        )
        self.assertNotIn("customer_id", properties)

    async def test_transfer_proposal_bridge_rejects_customer_override(self) -> None:
        token = create_access_token(42)
        session = AsyncMock()
        proposal = {
            "request_id": "d72132e2-cf94-44b2-a4b2-64ad82d07f71",
            "source_account_id": "ACC_001",
            "destination_account_id": "ACC_002",
            "amount": "100.00",
            "currency": "INR",
            "status": "PENDING",
            "expires_at": "2026-10-09T21:15:00+05:30",
            "applied": False,
            "source_transaction_id": None,
            "destination_transaction_id": None,
        }
        with patch("app.mcp.banking_commands.async_session") as session_factory:
            with patch(
                "app.mcp.banking_commands.propose_transfer",
                new=AsyncMock(return_value=proposal),
            ) as propose:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp/tools/propose_internal_transfer",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "source_account_id": "ACC_001",
                            "destination_account_id": "ACC_002",
                            "amount": "100.00",
                            "idempotency_key": "transfer-demo-001",
                        },
                    )
                    override = client.post(
                        "/mcp/tools/propose_internal_transfer",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "source_account_id": "ACC_001",
                            "destination_account_id": "ACC_002",
                            "amount": "100.00",
                            "idempotency_key": "transfer-demo-002",
                            "customer_id": 99,
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(override.status_code, 422)
        propose.assert_awaited_once_with(
            session,
            42,
            "ACC_001",
            "ACC_002",
            Decimal("100.00"),
            "transfer-demo-001",
        )

    async def test_service_request_proposal_is_available_through_mcp_bridge(self) -> None:
        token = create_access_token(42)
        session = AsyncMock()
        proposal = {
            "request_id": "d72132e2-cf94-44b2-a4b2-64ad82d07f71",
            "request_type": "STATEMENT",
            "status": "PENDING",
            "expires_at": "2026-10-09T21:15:00+05:30",
            "applied": False,
            "service_request_id": None,
        }
        with patch("app.mcp.banking_commands.async_session") as session_factory:
            with patch(
                "app.mcp.banking_commands.propose_request",
                new=AsyncMock(return_value=proposal),
            ) as propose:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp/tools/create_service_request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "request_type": "STATEMENT",
                            "idempotency_key": "service-demo-001",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        propose.assert_awaited_once_with(
            session,
            42,
            "STATEMENT",
            "service-demo-001",
        )

    async def test_service_request_confirm_route_requires_explicit_true(self) -> None:
        token = create_access_token(42)
        proposal_id = UUID("d72132e2-cf94-44b2-a4b2-64ad82d07f71")
        session = AsyncMock()
        result = {
            "request_id": str(proposal_id),
            "request_type": "STATEMENT",
            "status": "CONFIRMED",
            "expires_at": "2026-10-09T21:15:00+05:30",
            "applied": True,
            "service_request_id": "SR_123",
        }
        with patch("app.api.mcp.async_session") as session_factory:
            with patch(
                "app.api.mcp.confirm_service_request",
                new=AsyncMock(return_value=result),
            ) as confirm:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp/tools/confirm_service_request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "request_id": str(proposal_id),
                            "confirmed": True,
                        },
                    )
                    rejected = client.post(
                        "/mcp/tools/confirm_service_request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "request_id": str(proposal_id),
                            "confirmed": False,
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rejected.status_code, 422)
        confirm.assert_awaited_once_with(
            session,
            42,
            proposal_id,
            True,
        )

    async def test_concurrent_tool_invocations_keep_contexts_isolated(self) -> None:
        session = AsyncMock()
        calls: list[tuple[int, int]] = []
        both_started = asyncio.Event()

        async def get_accounts(_session: object, customer_id: int) -> list[object]:
            self.assertIs(_session, session)
            calls.append((customer_id, get_authenticated_customer_id()))
            if len(calls) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
            await asyncio.sleep(0)
            return []

        with patch("app.mcp.accounts.async_session") as session_factory:
            with patch(
                "app.mcp.accounts.get_customer_accounts",
                new=AsyncMock(side_effect=get_accounts),
            ):
                session_factory.return_value.__aenter__.return_value = session
                await asyncio.gather(
                    call_authenticated_mcp_tool(
                        AuthenticatedExecutionContext(101),
                        "get_my_accounts",
                    ),
                    call_authenticated_mcp_tool(
                        AuthenticatedExecutionContext(202),
                        "get_my_accounts",
                    ),
                )

        self.assertCountEqual(calls, [(101, 101), (202, 202)])

    async def test_existing_rest_authentication_still_uses_verified_customer(self) -> None:
        session = AsyncMock()

        async def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(app.dependency_overrides.pop, get_db, None)
        with patch(
            "app.api.transactions.get_customer_transaction_page",
            new=AsyncMock(
                return_value=TransactionPage(transactions=[], next_offset=None)
            ),
        ) as get_transactions:
            with TestClient(app) as client:
                unauthorized = client.get("/transactions/me")
                response = client.get(
                    "/transactions/me",
                    headers={
                        "Authorization": f"Bearer {create_access_token(42)}",
                    },
                )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"transactions": [], "limit": 25, "offset": 0, "next_offset": None},
        )
        get_transactions.assert_awaited_once_with(session, 42, limit=25, offset=0)


if __name__ == "__main__":
    unittest.main()
