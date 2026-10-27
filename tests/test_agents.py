"""Tests for deterministic coordinator and agent orchestration."""

import unittest
from unittest.mock import AsyncMock, patch

from app.agents import (
    AccountsAgent,
    Coordinator,
    DomainExecutionResult,
    ServiceAgent,
    TransactionsAgent,
    UnsupportedDomainError,
)
from app.mcp.context import (
    AuthenticatedExecutionContext,
    get_authenticated_customer_id,
)
from app.mcp.server import mcp_server


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_agent_map_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedDomainError):
            Coordinator({"get_my_accounts": AsyncMock()})  # type: ignore[dict-item]

    async def test_missing_requested_agent_is_rejected_before_any_dispatch(self) -> None:
        account_agent = AsyncMock()
        coordinator = Coordinator({"accounts": account_agent})

        with self.assertRaises(UnsupportedDomainError):
            coordinator.validate_domains(["accounts", "transactions"])

        account_agent.execute.assert_not_awaited()

    async def test_execute_domain_returns_sanitized_failure(self) -> None:
        account_agent = AsyncMock()
        account_agent.execute.side_effect = RuntimeError(
            "secret SQL and credentials"
        )
        coordinator = Coordinator({"accounts": account_agent})
        context = AuthenticatedExecutionContext(42)

        result = await coordinator.execute_domain_result(
            "accounts",
            context,
        )

        self.assertEqual(
            result,
            DomainExecutionResult(
                domain="accounts",
                status="failed",
                data=None,
                failure_code="operation_failed",
            ),
        )
        self.assertNotIn("secret SQL", repr(result))
        account_agent.execute.assert_awaited_once_with(context)

    async def test_execute_domain_returns_success_and_empty_results(self) -> None:
        account_agent = AsyncMock()
        account_agent.execute.return_value = {"result": []}
        coordinator = Coordinator({"accounts": account_agent})
        context = AuthenticatedExecutionContext(42)

        result = await coordinator.execute_domain_result("accounts", context)

        self.assertEqual(
            result,
            DomainExecutionResult(
                domain="accounts",
                status="empty",
                data={"result": []},
            ),
        )
        account_agent.execute.assert_awaited_once_with(context)

    async def test_unexpected_programming_errors_are_not_hidden(self) -> None:
        account_agent = AsyncMock()
        account_agent.execute.side_effect = ValueError("invalid agent result")
        coordinator = Coordinator({"accounts": account_agent})

        with self.assertRaisesRegex(ValueError, "invalid agent result"):
            await coordinator.execute_domain_result(
                "accounts",
                AuthenticatedExecutionContext(42),
            )

    async def test_customer_id_is_not_an_authorization_argument(self) -> None:
        coordinator = Coordinator()
        with self.assertRaises(TypeError):
            await coordinator.execute_domain_result(
                "accounts",
                AuthenticatedExecutionContext(42),
                customer_id=99,  # type: ignore[call-arg]
            )


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agents_invoke_their_existing_mcp_operations(self) -> None:
        context = AuthenticatedExecutionContext(42)
        result = {"result": []}
        cases = (
            (AccountsAgent, "get_my_accounts"),
            (TransactionsAgent, "get_my_transactions"),
            (ServiceAgent, "get_my_service_requests"),
        )

        for agent_type, tool_name in cases:
            with self.subTest(agent=agent_type.__name__):
                invoke_tool = AsyncMock(return_value=result)
                agent = agent_type(tool_invoker=invoke_tool)

                actual = await agent.execute(context)

                self.assertIs(actual, result)
                invoke_tool.assert_awaited_once_with(context, tool_name)

    async def test_authenticated_context_reaches_each_in_process_mcp_tool(self) -> None:
        context = AuthenticatedExecutionContext(73)
        observed: list[tuple[str, int]] = []

        async def observe_tool(
            tool_name: str,
            arguments: dict[str, object],
        ) -> tuple[list[object], dict[str, object]]:
            self.assertEqual(arguments, {})
            observed.append((tool_name, get_authenticated_customer_id()))
            return [], {"result": []}

        with patch.object(mcp_server, "call_tool", new=observe_tool):
            for agent_type in (AccountsAgent, TransactionsAgent, ServiceAgent):
                await agent_type().execute(context)

        self.assertCountEqual(
            observed,
            [
                ("get_my_accounts", 73),
                ("get_my_transactions", 73),
                ("get_my_service_requests", 73),
            ],
        )

    async def test_agents_do_not_accept_customer_id(self) -> None:
        agent = AccountsAgent(tool_invoker=AsyncMock())
        with self.assertRaises(TypeError):
            agent.execute(
                AuthenticatedExecutionContext(42),
                customer_id=99,  # type: ignore[call-arg]
            )

    async def test_missing_execution_context_fails_closed(self) -> None:
        for agent_type in (AccountsAgent, TransactionsAgent, ServiceAgent):
            with self.subTest(agent=agent_type.__name__):
                agent = agent_type()
                with self.assertRaises(TypeError):
                    await agent.execute(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
