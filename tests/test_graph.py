"""Tests for graph state and the minimal LangGraph skeleton."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from app.agents.coordinator import DomainExecutionResult
from app.graph import (
    BankingGraphState,
    GraphStateValidationError,
    aggregate_execution_result,
    build_graph,
    merge_domain_results,
)
from app.llm.schemas import Intent
from app.mcp.context import AuthenticatedExecutionContext


class GraphStateTests(unittest.TestCase):
    def test_state_accepts_intent_and_execution_fields(self) -> None:
        intent = Intent(outcome="supported", domains=["accounts"])
        state: BankingGraphState = {
            "intent": intent,
            "domain_results": {},
            "execution_status": "pending",
        }

        self.assertIs(state["intent"], intent)
        self.assertEqual(state["domain_results"], {})
        self.assertEqual(state["execution_status"], "pending")
        self.assertNotIn("customer_id", BankingGraphState.__annotations__)
        self.assertNotIn("jwt", BankingGraphState.__annotations__)

    def test_reducer_merges_disjoint_domain_results_in_canonical_order(
        self,
    ) -> None:
        accounts = {
            "accounts": {"status": "empty", "data": {"result": []}}
        }
        transactions = {
            "transactions": {
                "status": "succeeded",
                "data": {"result": [{"amount": "1.00"}]},
            }
        }

        merged = merge_domain_results(transactions, accounts)

        self.assertEqual(
            list(merged),
            ["accounts", "transactions"],
        )
        self.assertEqual(merged["accounts"]["status"], "empty")
        self.assertEqual(merged["transactions"]["status"], "succeeded")
        self.assertEqual(transactions.keys(), {"transactions"})

    def test_reducer_rejects_duplicate_domain_updates(self) -> None:
        existing = {
            "accounts": {"status": "empty", "data": {"result": []}}
        }
        update = {
            "accounts": {"status": "empty", "data": {"result": []}}
        }

        with self.assertRaisesRegex(
            GraphStateValidationError,
            "Duplicate domain result",
        ):
            merge_domain_results(existing, update)

    def test_reducer_rejects_unsupported_or_malformed_updates(self) -> None:
        invalid_updates = (
            {"payments": {"status": "empty", "data": {"result": []}}},
            {"accounts": {"status": "failed", "data": None}},
            {
                "accounts": {
                    "status": "failed",
                    "data": None,
                    "failure_code": "database detail",
                }
            },
            {
                "accounts": {
                    "status": "succeeded",
                    "data": {"result": []},
                    "customer_id": 1,
                }
            },
        )

        for update in invalid_updates:
            with self.subTest(update=update):
                with self.assertRaises(GraphStateValidationError):
                    merge_domain_results({}, update)  # type: ignore[arg-type]


class GraphBuilderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.coordinator = AsyncMock()
        self.coordinator.validate_domains = Mock()
        self.context = AuthenticatedExecutionContext(42)
        self.graph = build_graph(self.coordinator, self.context)

    async def test_graph_dispatches_intent_and_returns_aggregate(self) -> None:
        intent = Intent(
            outcome="supported",
            domains=["accounts", "transactions"],
        )
        results = {
            "accounts": DomainExecutionResult(
                domain="accounts",
                status="empty",
                data={"result": []},
            ),
            "transactions": DomainExecutionResult(
                domain="transactions",
                status="succeeded",
                data={"result": [{"amount": "1.00"}]},
            ),
        }
        async def execute_domain(domain: str, _context: object):
            return results[domain]

        self.coordinator.execute_domain_result.side_effect = execute_domain
        result = await self.graph.ainvoke({"intent": intent})

        self.assertEqual(result["intent"], intent)
        self.assertEqual(result["execution_status"], "succeeded")
        self.assertEqual(result["graph_status"], "completed")
        aggregate = aggregate_execution_result(result)
        self.assertEqual(aggregate.status, "succeeded")
        self.assertEqual(
            [item.domain for item in aggregate.results],
            ["accounts", "transactions"],
        )
        self.assertEqual(
            {
                call.args[0]
                for call in self.coordinator.execute_domain_result.await_args_list
            },
            {"accounts", "transactions"},
        )
        self.assertTrue(
            all(
                call.args[1] is self.context
                for call in self.coordinator.execute_domain_result.await_args_list
            )
        )

    async def test_domain_nodes_run_in_parallel(self) -> None:
        started: set[str] = set()
        all_started = asyncio.Event()
        release = asyncio.Event()

        async def execute_domain(domain: str, _context: object):
            started.add(domain)
            if len(started) == 3:
                all_started.set()
            await release.wait()
            return DomainExecutionResult(
                domain=domain,  # type: ignore[arg-type]
                status="empty",
                data={"result": []},
            )

        self.coordinator.execute_domain_result.side_effect = execute_domain
        intent = Intent(
            outcome="supported",
            domains=["accounts", "transactions", "service_requests"],
        )
        execution = asyncio.create_task(self.graph.ainvoke({"intent": intent}))

        try:
            await asyncio.wait_for(all_started.wait(), timeout=1)
        finally:
            release.set()

        result = await execution
        self.assertEqual(
            started,
            {"accounts", "transactions", "service_requests"},
        )
        self.assertEqual(result["execution_status"], "succeeded")
        self.assertEqual(
            [result.domain for result in aggregate_execution_result(result).results],
            ["accounts", "transactions", "service_requests"],
        )

    async def test_graph_aggregates_partial_domain_failure(self) -> None:
        results = {
            "accounts": DomainExecutionResult(
                domain="accounts",
                status="failed",
                data=None,
                failure_code="operation_failed",
            ),
            "transactions": DomainExecutionResult(
                domain="transactions",
                status="succeeded",
                data={"result": [{"amount": "1.00"}]},
            ),
        }

        async def execute_domain(domain: str, _context: object):
            return results[domain]

        self.coordinator.execute_domain_result.side_effect = execute_domain
        state = await self.graph.ainvoke(
            {
                "intent": Intent(
                    outcome="supported",
                    domains=["accounts", "transactions"],
                )
            }
        )

        self.assertEqual(state["execution_status"], "partial_failure")
        aggregate = aggregate_execution_result(state)
        self.assertEqual(aggregate.status, "partial_failure")
        self.assertEqual(
            [result.status for result in aggregate.results],
            ["failed", "succeeded"],
        )

    async def test_graph_rejects_missing_intent(self) -> None:
        with self.assertRaisesRegex(
            GraphStateValidationError,
            "requires an intent",
        ):
            await self.graph.ainvoke(
                {
                    "domain_results": {},
                    "execution_status": "pending",
                }
            )

    async def test_graph_rejects_malformed_intent(self) -> None:
        with self.assertRaises(GraphStateValidationError):
            await self.graph.ainvoke(
                {
                    "intent": {
                        "outcome": "supported",
                        "domains": ["payments"],
                    },
                    "domain_results": {},
                    "execution_status": "pending",
                }
            )

    async def test_graph_rejects_malformed_existing_state(self) -> None:
        with self.assertRaisesRegex(
            GraphStateValidationError,
            "execution status is invalid",
        ):
            await self.graph.ainvoke(
                {
                    "intent": Intent(outcome="unknown", domains=[]),
                    "domain_results": {},
                    "execution_status": "working",
                }
            )

    async def test_graph_does_not_dispatch_non_supported_intents(self) -> None:
        intent = Intent(outcome="unsupported", domains=[])
        result = await self.graph.ainvoke({"intent": intent})

        self.assertEqual(result["execution_status"], "not_dispatched")
        self.assertEqual(result["domain_results"], {})
        self.coordinator.execute_domain_result.assert_not_awaited()

    async def test_graph_rejects_caller_supplied_domain_results(self) -> None:
        intent = Intent(outcome="supported", domains=["accounts"])

        with self.assertRaisesRegex(
            GraphStateValidationError,
            "must be empty before execution",
        ):
            await self.graph.ainvoke(
                {
                    "intent": intent,
                    "domain_results": {
                        "accounts": {
                            "status": "empty",
                            "data": {"result": []},
                        }
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
