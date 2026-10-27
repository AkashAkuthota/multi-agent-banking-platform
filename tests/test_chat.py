"""Tests for the authenticated end-to-end chat endpoint."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.agents import (
    Coordinator,
    DomainExecutionResult,
)
from app.core.config import settings
from app.core.jwt import create_access_token
from app.llm.client import IntentClassificationError
from app.llm.presentation import AccountsPresentationData
from app.llm.response_generator import ResponseGenerationError
from app.llm.router import IntentRouter
from app.llm.schemas import (
    CommandAction,
    Intent,
)
from app.main import app
from app.mcp.context import get_authenticated_customer_id
from app.models import Account
from app.schemas.chat import ChatResponse
from app.services.chat_session import (
    ChatSessionState,
    InMemoryChatSessionStore,
    SessionAccount,
)


class ChatEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = (
            patch.object(
                settings,
                "JWT_SECRET_KEY",
                "test-secret-key-for-auth-tests-long-enough",
            ),
            patch.object(settings, "JWT_ALGORITHM", "HS256"),
            patch.object(settings, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 5),
        )
        for setting_patch in self.settings:
            setting_patch.start()
            self.addCleanup(setting_patch.stop)
        from app.api.chat import get_chat_session_store

        self.session_store = InMemoryChatSessionStore()
        app.dependency_overrides[get_chat_session_store] = lambda: self.session_store
        self.addCleanup(
            app.dependency_overrides.pop,
            get_chat_session_store,
            None,
        )

    def install_router(
        self,
        intent_router: IntentRouter,
        coordinator: Coordinator | AsyncMock | None = None,
        response_generator: AsyncMock | None = None,
    ) -> AsyncMock:
        from app.api.chat import (
            get_coordinator,
            get_intent_router,
            get_response_generator,
        )

        app.dependency_overrides[get_intent_router] = lambda: intent_router
        self.addCleanup(app.dependency_overrides.pop, get_intent_router, None)
        if response_generator is None:
            response_generator = AsyncMock()
            response_generator.generate.return_value = (
                "Your current account balance is INR 52340.00."
            )
        app.dependency_overrides[get_response_generator] = (
            lambda: response_generator
        )
        self.addCleanup(
            app.dependency_overrides.pop,
            get_response_generator,
            None,
        )
        if coordinator is not None:
            if isinstance(coordinator, AsyncMock):
                coordinator.validate_domains = Mock()
            app.dependency_overrides[get_coordinator] = lambda: coordinator
            self.addCleanup(app.dependency_overrides.pop, get_coordinator, None)
        return response_generator

    @staticmethod
    def auth_headers(customer_id: int = 1) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {create_access_token(customer_id)}",
        }

    def test_chat_requires_authentication(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        intent_router = IntentRouter(provider)
        response_generator = self.install_router(intent_router)

        with TestClient(app) as client:
            response = client.post("/chat", json={"message": "What's my balance?"})

        self.assertEqual(response.status_code, 401)
        provider.classify.assert_not_awaited()
        response_generator.generate.assert_not_awaited()

    def test_chat_rejects_invalid_jwt(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        response_generator = self.install_router(IntentRouter(provider))

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers={"Authorization": "Bearer invalid-token"},
                json={"message": "Show my balance"},
            )

        self.assertEqual(response.status_code, 401)
        provider.classify.assert_not_awaited()
        response_generator.generate.assert_not_awaited()

    def test_full_authenticated_flow_preserves_jwt_customer_through_mcp(self) -> None:
        context_ids: list[int] = []
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        intent_router = IntentRouter(provider)
        response_generator = self.install_router(intent_router)

        async def get_accounts(_session: object, customer_id: int) -> list[object]:
            self.assertIs(_session, session)
            context_ids.append(get_authenticated_customer_id())
            self.assertEqual(customer_id, 1)
            return [
                SimpleNamespace(
                    account_id="ACC_INTERNAL_1",
                    account_type="SAVINGS",
                    account_number="4111111111111111",
                    balance=Decimal("52340.00"),
                    currency="INR",
                    status="ACTIVE",
                    created_at=datetime(
                        2026,
                        10,
                        8,
                        tzinfo=timezone.utc,
                    ),
                )
            ]

        session = AsyncMock()
        with patch("app.mcp.accounts.async_session") as session_factory:
            with patch(
                "app.mcp.accounts.get_customer_accounts",
                new=AsyncMock(side_effect=get_accounts),
            ) as service:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/chat",
                        headers=self.auth_headers(1),
                        json={
                            "message": "What is my account balance?",
                            "customer_id": 2,
                        },
                    )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(context_ids, [])
        provider.classify.assert_not_awaited()
        service.assert_not_awaited()

        # A strict request rejects caller-supplied identity; the valid request uses
        # the verified JWT identity all the way through the MCP/service boundary.
        with patch("app.mcp.accounts.async_session") as session_factory:
            with patch(
                "app.mcp.accounts.get_customer_accounts",
                new=AsyncMock(side_effect=get_accounts),
            ) as service:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    response = client.post(
                        "/chat",
                        headers=self.auth_headers(1),
                        json={"message": "What is my account balance?"},
                    )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "domain": "accounts",
                "answer": "Your current account balance is INR 52340.00.",
            },
        )
        self.assertEqual(context_ids, [1])
        provider.classify.assert_awaited_once_with("What is my account balance?")
        service.assert_awaited_once_with(session, 1)
        response_generator.generate.assert_awaited_once()
        presentation_data = response_generator.generate.await_args.args[0]
        self.assertIsInstance(presentation_data, AccountsPresentationData)
        self.assertEqual(presentation_data.accounts[0].balance, "52340.00")
        self.assertNotIn("customer_id", repr(presentation_data))
        self.assertNotIn("ACC_INTERNAL_1", repr(presentation_data))
        self.assertNotIn("4111111111111111", repr(presentation_data))

    def test_customers_one_and_two_receive_only_their_own_service_data(self) -> None:
        seen_context_ids: list[int] = []
        seen_service_ids: list[int] = []
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        router = IntentRouter(provider)
        response_generator = AsyncMock()
        response_generator.generate.side_effect = [
            "Customer one's synthetic balance is INR 10.00.",
            "Customer two's synthetic balance is INR 20.00.",
        ]
        self.install_router(router, response_generator=response_generator)
        session = AsyncMock()
        accounts = {
            1: Account(
                id=1,
                account_id="SYNTHETIC_ACCOUNT_ONE",
                customer_id=1,
                account_type="SAVINGS",
                account_number="SYNTHETIC_NUMBER_ONE",
                balance=Decimal("10.00"),
                currency="INR",
                status="ACTIVE",
                created_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            ),
            2: Account(
                id=2,
                account_id="SYNTHETIC_ACCOUNT_TWO",
                customer_id=2,
                account_type="SAVINGS",
                account_number="SYNTHETIC_NUMBER_TWO",
                balance=Decimal("20.00"),
                currency="INR",
                status="ACTIVE",
                created_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            ),
        }

        async def get_accounts(
            _session: object,
            customer_id: int,
        ) -> list[Account]:
            self.assertIs(_session, session)
            context_customer_id = get_authenticated_customer_id()
            self.assertEqual(context_customer_id, customer_id)
            seen_context_ids.append(context_customer_id)
            seen_service_ids.append(customer_id)
            return [accounts[customer_id]]

        with patch("app.mcp.accounts.async_session") as session_factory:
            with patch(
                "app.mcp.accounts.get_customer_accounts",
                new=AsyncMock(side_effect=get_accounts),
            ) as service:
                session_factory.return_value.__aenter__.return_value = session
                with TestClient(app) as client:
                    first_token = create_access_token(1)
                    second_token = create_access_token(2)
                    first = client.post(
                        "/chat",
                        headers={"Authorization": f"Bearer {first_token}"},
                        json={"message": "Show my balance"},
                    )
                    second = client.post(
                        "/chat",
                        headers={"Authorization": f"Bearer {second_token}"},
                        json={"message": "Show my balance"},
                    )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(seen_context_ids, [1, 2])
        self.assertEqual(seen_service_ids, [1, 2])
        self.assertEqual(
            [
                call.args[0].accounts[0].balance
                for call in response_generator.generate.await_args_list
            ],
            ["10.00", "20.00"],
        )
        service.assert_awaited()
        self.assertEqual(
            [call.args for call in provider.classify.await_args_list],
            [("Show my balance",), ("Show my balance",)],
        )
        forbidden_inputs = repr(
            (
                provider.classify.await_args_list,
                response_generator.generate.await_args_list,
            )
        )
        self.assertNotIn(first_token, forbidden_inputs)
        self.assertNotIn(second_token, forbidden_inputs)
        self.assertNotIn("customer_id", forbidden_inputs)
        self.assertNotIn("SYNTHETIC_ACCOUNT", forbidden_inputs)
        self.assertNotIn("SYNTHETIC_NUMBER", forbidden_inputs)

    def test_customer_id_query_parameter_is_rejected(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        self.install_router(IntentRouter(provider))

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(1),
                params={"customer_id": 2},
                json={"message": "Show my balance"},
            )

        self.assertEqual(response.status_code, 422)
        provider.classify.assert_not_awaited()

    def test_unknown_intent_does_not_invoke_agent(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(outcome="unknown", domains=[])
        coordinator = AsyncMock()
        intent_router = IntentRouter(provider)
        response_generator = self.install_router(intent_router, coordinator)

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "Tell me about the weather"},
            )

        self.assertEqual(response.status_code, 422)
        coordinator.execute_domain_result.assert_not_awaited()
        response_generator.generate.assert_not_awaited()

    def test_multi_domain_intent_runs_and_reports_each_requested_domain(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts", "transactions"],
        )
        coordinator = AsyncMock()
        results = {
            "accounts": DomainExecutionResult(
                domain="accounts",
                status="empty",
                data={"result": []},
            ),
            "transactions": DomainExecutionResult(
                domain="transactions",
                status="empty",
                data={"result": []},
            ),
        }

        async def execute_domain(domain: str, _context: object):
            return results[domain]

        coordinator.execute_domain_result.side_effect = execute_domain
        response_generator = AsyncMock()
        response_generator.generate.return_value = "Your account and transaction results are empty."
        response_generator = self.install_router(
            IntentRouter(provider),
            coordinator,
            response_generator,
        )

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "Show my balance and recent transactions."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "domains": ["accounts", "transactions"],
                "answer": "Your account and transaction results are empty.",
            },
        )
        self.assertEqual(coordinator.execute_domain_result.await_count, 2)
        self.assertEqual(
            [result.domain for result in response_generator.generate.await_args.args[0].results],
            ["accounts", "transactions"],
        )

    def test_openai_failure_returns_controlled_503_without_coordinator_call(self) -> None:
        provider = AsyncMock()
        provider.classify.side_effect = IntentClassificationError(
            "internal provider details must not leak"
        )
        coordinator = AsyncMock()
        response_generator = self.install_router(
            IntentRouter(provider),
            coordinator,
        )

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "Show my recent transactions"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Intent classification is unavailable")
        self.assertNotIn("internal provider details", response.text)
        coordinator.execute_domain_result.assert_not_awaited()
        response_generator.generate.assert_not_awaited()

    def test_blank_and_oversized_messages_are_rejected(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        response_generator = self.install_router(IntentRouter(provider))

        with TestClient(app) as client:
            blank = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "   "},
            )
            oversized = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "x" * 4001},
            )

        self.assertEqual(blank.status_code, 422)
        self.assertEqual(oversized.status_code, 422)
        provider.classify.assert_not_awaited()
        response_generator.generate.assert_not_awaited()

    def test_service_failure_returns_sanitized_500(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        coordinator = AsyncMock()
        coordinator.execute_domain_result.side_effect = ValueError(
            "database connection string must not leak"
        )
        response_generator = self.install_router(
            IntentRouter(provider),
            coordinator,
        )

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "Show my accounts"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"],
            "Banking request could not be completed",
        )
        self.assertNotIn("database connection string", response.text)
        response_generator.generate.assert_not_awaited()

    def test_response_generation_failure_returns_controlled_503(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        response_generator = AsyncMock()
        response_generator.generate.side_effect = ResponseGenerationError(
            "internal OpenAI response detail must not leak"
        )
        coordinator = AsyncMock()
        coordinator.execute_domain_result.return_value = DomainExecutionResult(
            domain="accounts",
            status="empty",
            data={"result": []},
        )
        self.install_router(
            IntentRouter(provider),
            coordinator,
            response_generator,
        )

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "Show my accounts"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Response generation is unavailable",
        )
        self.assertNotIn("internal OpenAI response detail", response.text)
        response_generator.generate.assert_awaited_once()

    def test_authorization_failure_status_is_preserved_without_internal_detail(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        coordinator = AsyncMock()
        coordinator.execute_domain_result.side_effect = HTTPException(
            status_code=403,
            detail="sensitive authorization implementation detail",
        )
        self.install_router(IntentRouter(provider), coordinator)

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "Show my accounts"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("sensitive authorization", response.text)

    def test_response_serializes_decimal_values(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        coordinator = AsyncMock()
        coordinator.execute_domain_result.return_value = DomainExecutionResult(
            domain="accounts",
            status="succeeded",
            data={
                "result": [
                    {
                        "account_type": "SAVINGS",
                        "balance": "100.25",
                        "currency": "INR",
                        "status": "ACTIVE",
                    }
                ]
            },
        )
        response_generator = self.install_router(
            IntentRouter(provider),
            coordinator,
        )

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"message": "Show my account balance"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["answer"],
            "Your current account balance is INR 52340.00.",
        )
        ChatResponse.model_validate(response.json())
        coordinator.execute_domain_result.assert_awaited_once()
        domain, execution_context = coordinator.execute_domain_result.await_args.args
        self.assertEqual(domain, "accounts")
        self.assertEqual(execution_context.customer_id, 1)
        response_generator.generate.assert_awaited_once()
        self.assertEqual(
            response_generator.generate.await_args.args[0].accounts[0].balance,
            "100.25",
        )

    def test_chat_creates_only_a_pending_command_proposal(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="daily_limit_change",
                account_id="ACC_001",
                requested_limit="20000.00",
            ),
        )
        response_generator = self.install_router(IntentRouter(provider))
        pending_id = UUID("66b74620-aefb-42ae-9c94-796a2120991c")
        proposal_tool = AsyncMock(
            return_value={
                "request_id": str(pending_id),
                "account_id": "ACC_001",
                "requested_limit": "20000.00",
                "status": "PENDING",
                "expires_at": "2026-10-09T22:00:00+00:00",
                "applied": False,
            }
        )

        with patch("app.api.chat.call_authenticated_mcp_tool", new=proposal_tool):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    headers={
                        **self.auth_headers(),
                        "Idempotency-Key": "chat-limit-001",
                    },
                    json={"message": "Increase the daily limit on ACC_001 to 20000."},
                )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["command"]["request_id"], str(pending_id))
        self.assertEqual(body["command"]["status"], "PENDING")
        self.assertFalse(body["command"]["applied"])
        self.assertIn(f"confirm {pending_id}", body["answer"])
        self.assertEqual(body["pending_proposal"]["request_id"], str(pending_id))
        self.assertEqual(proposal_tool.await_args.args[1], "propose_daily_limit_change")
        self.assertEqual(
            proposal_tool.await_args.args[2]["account_id"],
            "ACC_001",
        )
        self.assertEqual(
            proposal_tool.await_args.args[2]["idempotency_key"],
            "chat-limit-001",
        )
        response_generator.generate.assert_not_awaited()

    def test_daily_limit_command_misclassified_as_read_returns_clarification(
        self,
    ) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        coordinator = AsyncMock()
        response_generator = self.install_router(
            IntentRouter(provider),
            coordinator,
        )
        account_lookup = AsyncMock(
            return_value={
                "result": [
                    {
                        "account_id": "ACC_DEMO_002",
                        "account_type": "SAVINGS",
                        "status": "ACTIVE",
                    },
                    {
                        "account_id": "ACC_001",
                        "account_type": "CURRENT",
                        "status": "ACTIVE",
                    },
                ]
            }
        )

        with patch("app.api.chat.call_authenticated_mcp_tool", new=account_lookup):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    headers=self.auth_headers(),
                    json={"message": "Increase my current daily limit to 20000"},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["clarification"], "daily_limit_details")
        self.assertEqual(
            response.json()["account_options"],
            [
                {"account_id": "ACC_001", "account_type": "CURRENT"},
                {"account_id": "ACC_DEMO_002", "account_type": "SAVINGS"},
            ],
        )
        self.assertIn("choose the account", response.json()["answer"])
        self.assertEqual(account_lookup.await_args.args[1], "get_my_accounts")
        coordinator.execute_domain_result.assert_not_awaited()
        response_generator.generate.assert_not_awaited()

    def test_chat_confirms_only_explicit_customer_proposal_id(self) -> None:
        provider = AsyncMock()
        proposal_id = UUID("332a1fd9-89c2-43b5-83c0-31564703d8b4")
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="confirm",
                request_id=str(proposal_id),
            ),
        )
        self.install_router(IntentRouter(provider))
        session = AsyncMock()
        session_factory_result = AsyncMock()
        session_factory_result.__aenter__.return_value = session
        action = AsyncMock(
            return_value={
                "request_id": str(proposal_id),
                "account_id": "ACC_001",
                "requested_limit": "20000.00",
                "status": "CONFIRMED",
                "expires_at": "2026-10-09T22:00:00+00:00",
                "applied": True,
            }
        )

        with patch("app.api.chat.async_session", return_value=session_factory_result):
            with patch("app.api.chat.decide_customer_proposal", new=action):
                with TestClient(app) as client:
                    response = client.post(
                        "/chat",
                        headers=self.auth_headers(),
                        json={"message": f"Confirm proposal {proposal_id}"},
                    )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["command"]["status"], "CONFIRMED")
        self.assertTrue(response.json()["command"]["applied"])
        action.assert_awaited_once_with(session, 1, proposal_id, "confirm")

    def test_chat_cancels_an_explicit_proposal_without_applying_it(self) -> None:
        provider = AsyncMock()
        proposal_id = UUID("b613e07a-713d-46db-b095-96dc05c6be4b")
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="cancel",
                request_id=str(proposal_id),
            ),
        )
        self.install_router(IntentRouter(provider))
        session = AsyncMock()
        session_factory_result = AsyncMock()
        session_factory_result.__aenter__.return_value = session
        action = AsyncMock(
            return_value={
                "request_id": str(proposal_id),
                "source_account_id": "ACC_001",
                "destination_account_id": "ACC_DEMO_002",
                "status": "CANCELLED",
                "expires_at": "2026-10-09T22:00:00+00:00",
                "applied": False,
            }
        )

        with patch("app.api.chat.async_session", return_value=session_factory_result):
            with patch("app.api.chat.decide_customer_proposal", new=action):
                with TestClient(app) as client:
                    response = client.post(
                        "/chat",
                        headers=self.auth_headers(),
                        json={"message": f"Cancel proposal {proposal_id}"},
                    )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["command"]["action"], "cancel")
        self.assertEqual(response.json()["command"]["status"], "CANCELLED")
        self.assertFalse(response.json()["command"]["applied"])
        action.assert_awaited_once_with(session, 1, proposal_id, "cancel")

    def test_chat_routes_transfer_and_service_proposals_through_mcp(self) -> None:
        proposal_ids = (
            UUID("c938717b-9d98-4d24-b0fa-aa896efc7146"),
            UUID("342fdf13-c113-4df3-aa61-f408c1903dc3"),
        )
        intents = (
            Intent(
                outcome="supported",
                domains=[],
                action=CommandAction(
                    kind="internal_transfer",
                    source_account_id="ACC_001",
                    destination_account_id="ACC_DEMO_002",
                    amount="100.00",
                ),
            ),
            Intent(
                outcome="supported",
                domains=[],
                action=CommandAction(
                    kind="service_request",
                    request_type="STATEMENT",
                ),
            ),
        )
        provider = AsyncMock()
        provider.classify.side_effect = intents
        self.install_router(IntentRouter(provider))
        proposal_tool = AsyncMock(
            side_effect=[
                {
                    "request_id": str(proposal_id),
                    "source_account_id": "ACC_001",
                    "destination_account_id": "ACC_DEMO_002",
                    "request_type": None,
                    "status": "PENDING",
                    "expires_at": "2026-10-09T22:00:00+00:00",
                    "applied": False,
                }
                if index == 0
                else {
                    "request_id": str(proposal_id),
                    "request_type": "STATEMENT",
                    "status": "PENDING",
                    "expires_at": "2026-10-09T22:00:00+00:00",
                    "applied": False,
                }
                for index, proposal_id in enumerate(proposal_ids)
            ]
        )

        with patch("app.api.chat.call_authenticated_mcp_tool", new=proposal_tool):
            with TestClient(app) as client:
                transfer = client.post(
                    "/chat",
                    headers={
                        **self.auth_headers(),
                        "Idempotency-Key": "chat-transfer-001",
                    },
                    json={"message": "Transfer 100 from ACC_001 to ACC_DEMO_002"},
                )
                service_request = client.post(
                    "/chat",
                    headers={
                        **self.auth_headers(),
                        "Idempotency-Key": "chat-service-001",
                    },
                    json={"message": "Request a statement"},
                )

        self.assertEqual(transfer.status_code, 200, transfer.text)
        self.assertEqual(service_request.status_code, 200, service_request.text)
        self.assertEqual(transfer.json()["command"]["kind"], "internal_transfer")
        self.assertFalse(transfer.json()["command"]["applied"])
        self.assertEqual(
            service_request.json()["command"]["kind"],
            "service_request",
        )
        self.assertFalse(service_request.json()["command"]["applied"])
        self.assertEqual(
            [call.args[1] for call in proposal_tool.await_args_list],
            ["propose_internal_transfer", "create_service_request"],
        )
        self.assertEqual(
            proposal_tool.await_args_list[0].args[2]["idempotency_key"],
            "chat-transfer-001",
        )
        self.assertEqual(
            proposal_tool.await_args_list[1].args[2]["idempotency_key"],
            "chat-service-001",
        )

    def test_chat_requires_idempotency_key_before_proposing(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="service_request",
                request_type="STATEMENT",
            ),
        )
        self.install_router(IntentRouter(provider))
        proposal_tool = AsyncMock()

        with patch("app.api.chat.call_authenticated_mcp_tool", new=proposal_tool):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    headers=self.auth_headers(),
                    json={"message": "Request a statement"},
                )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Idempotency-Key", response.json()["detail"])
        proposal_tool.assert_not_awaited()

    def test_chat_retry_reports_an_already_applied_idempotent_proposal(self) -> None:
        provider = AsyncMock()
        proposal_id = UUID("7226ed20-c416-4b79-9b87-3c1834a9d9b4")
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="service_request",
                request_type="STATEMENT",
            ),
        )
        self.install_router(IntentRouter(provider))
        proposal_tool = AsyncMock(
            return_value={
                "request_id": str(proposal_id),
                "request_type": "STATEMENT",
                "status": "CONFIRMED",
                "expires_at": "2026-10-09T22:00:00+00:00",
                "applied": True,
                "service_request_id": "SR_123",
            }
        )

        with patch("app.api.chat.call_authenticated_mcp_tool", new=proposal_tool):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    headers={
                        **self.auth_headers(),
                        "Idempotency-Key": "chat-service-retry-001",
                    },
                    json={"message": "Request a statement"},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["command"]["applied"])
        self.assertIn("already confirmed and applied", response.json()["answer"])
        self.assertNotIn("No change has been applied", response.json()["answer"])

    def test_chat_does_not_confirm_without_a_proposal_id(self) -> None:
        provider = AsyncMock()
        pending_id = UUID("66b74620-aefb-42ae-9c94-796a2120991c")
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(kind="confirm", request_id=str(pending_id)),
        )
        self.install_router(IntentRouter(provider))
        action = AsyncMock()

        async def seed_pending() -> None:
            await self.session_store.save(
                1,
                ChatSessionState(
                    pending_proposal_id=pending_id,
                    pending_proposal_kind="daily_limit_change",
                    pending_proposal_expires_at="2099-01-01T00:00:00+00:00",
                ),
            )

        self._run(seed_pending())
        with patch("app.api.chat.decide_customer_proposal", new=action):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    headers=self.auth_headers(),
                    json={"message": "Yes, confirm it"},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["clarification"], "proposal_id")
        self.assertIn("yes is not enough", response.json()["answer"])
        action.assert_not_awaited()

    def test_pending_proposal_confirm_uses_session_id_not_chat_text(self) -> None:
        proposal_id = UUID("332a1fd9-89c2-43b5-83c0-31564703d8b4")

        async def seed_pending() -> None:
            await self.session_store.save(
                1,
                ChatSessionState(
                    pending_proposal_id=proposal_id,
                    pending_proposal_kind="daily_limit_change",
                    pending_proposal_expires_at="2099-01-01T00:00:00+00:00",
                ),
            )

        self._run(seed_pending())
        session = AsyncMock()
        session_factory_result = AsyncMock()
        session_factory_result.__aenter__.return_value = session
        action = AsyncMock(
            return_value={
                "request_id": str(proposal_id),
                "account_id": "ACC_001",
                "requested_limit": "20000.00",
                "status": "CONFIRMED",
                "expires_at": "2026-10-09T22:00:00+00:00",
                "applied": True,
            }
        )

        with patch("app.api.chat.async_session", return_value=session_factory_result):
            with patch("app.api.chat.decide_customer_proposal", new=action):
                with TestClient(app) as client:
                    response = client.post(
                        "/chat/pending-proposal/confirm",
                        headers=self.auth_headers(),
                    )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["command"]["applied"])
        self.assertNotIn("pending_proposal", response.json())
        action.assert_awaited_once_with(session, 1, proposal_id, "confirm")

    def test_chat_resolves_the_other_account_from_session_state(self) -> None:
        provider = AsyncMock()
        self.install_router(IntentRouter(provider))

        async def seed_accounts() -> None:
            await self.session_store.save(
                1,
                ChatSessionState(
                    accounts=[
                        SessionAccount(account_id="ACC_001", account_type="CURRENT"),
                        SessionAccount(
                            account_id="ACC_DEMO_002",
                            account_type="SAVINGS",
                        ),
                    ],
                    last_referenced_account_id="ACC_001",
                    last_requested_limit="20000",
                ),
            )

        self._run(seed_accounts())
        proposal_id = UUID("aa111111-1111-4111-8111-111111111111")
        proposal_tool = AsyncMock(
            return_value={
                "request_id": str(proposal_id),
                "account_id": "ACC_DEMO_002",
                "requested_limit": "20000",
                "status": "PENDING",
                "expires_at": "2026-10-09T22:00:00+00:00",
                "applied": False,
            }
        )

        with patch("app.api.chat.call_authenticated_mcp_tool", new=proposal_tool):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    headers={
                        **self.auth_headers(),
                        "Idempotency-Key": "other-account-001",
                    },
                    json={
                        "message": "Increase the daily limit on the other account to 20000"
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            proposal_tool.await_args.args[2]["account_id"],
            "ACC_DEMO_002",
        )
        provider.classify.assert_not_awaited()

    @staticmethod
    def _run(awaitable):
        import asyncio

        asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()
