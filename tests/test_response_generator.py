"""Tests for sanitized banking response generation."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import httpx
from openai import AsyncOpenAI, APIConnectionError

from app.agents.coordinator import (
    AggregateExecutionResult,
    DomainExecutionResult,
)
from app.core.config import settings
from app.core.jwt import create_access_token
from app.llm.client import (
    GeminiStructuredOutputClient,
    OllamaStructuredOutputClient,
    OpenAIResponsesClient,
)
from app.llm.presentation import (
    AggregatePresentationData,
    AccountsPresentationData,
    PresentationDataError,
    ServiceRequestsPresentationData,
    TransactionsPresentationData,
    build_aggregate_presentation_data,
    build_presentation_data,
)
from app.llm.response_generator import (
    RESPONSE_INSTRUCTIONS,
    ResponseGenerationError,
    ResponseGenerator,
)
from app.mcp.server import mcp_server


class ResponseGeneratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.model_patch = unittest.mock.patch.object(
            settings,
            "OPENAI_MODEL",
            "test-model",
        )
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.client = create_autospec(AsyncOpenAI, instance=True)
        self.client.responses.parse = AsyncMock()
        self.generator = ResponseGenerator(OpenAIResponsesClient(self.client))

    @staticmethod
    def aggregate(
        *results: DomainExecutionResult,
        status: str = "succeeded",
        outcome: str = "supported",
    ) -> AggregateExecutionResult:
        return AggregateExecutionResult(
            outcome=outcome,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            results=results,
        )

    @staticmethod
    def account_result() -> DomainExecutionResult:
        return DomainExecutionResult(
            domain="accounts",
            status="succeeded",
            data={
                "result": [
                    {
                        "account_id": "INTERNAL_ACCOUNT",
                        "account_number": "SYNTHETIC_ACCOUNT_NUMBER",
                        "customer_id": 999,
                        "id": 123,
                        "account_type": "SAVINGS",
                        "balance": "12.50",
                        "currency": "INR",
                        "status": "ACTIVE",
                        "created_at": "2026-10-09T00:00:00+00:00",
                        "password": "synthetic-password",
                        "sql": "SELECT * FROM accounts",
                    }
                ]
            },
        )

    @staticmethod
    def transaction_result() -> DomainExecutionResult:
        return DomainExecutionResult(
            domain="transactions",
            status="succeeded",
            data={
                "result": [
                    {
                        "transaction_id": "INTERNAL_TRANSACTION",
                        "account_id": 10,
                        "transaction_type": "DEBIT",
                        "amount": "2.00",
                        "currency": "INR",
                        "merchant": "Synthetic Cafe",
                        "category": "Food",
                        "transaction_date": "2026-10-08T11:00:00+00:00",
                        "status": "COMPLETED",
                        "description": "Synthetic coffee",
                    }
                ]
            },
        )

    @staticmethod
    def service_request_result() -> DomainExecutionResult:
        return DomainExecutionResult(
            domain="service_requests",
            status="succeeded",
            data={
                "result": [
                    {
                        "request_id": "INTERNAL_REQUEST",
                        "customer_id": 999,
                        "request_type": "STATEMENT",
                        "status": "PENDING",
                        "created_at": "2026-10-08T12:00:00+00:00",
                        "completed_at": None,
                        "credential_hash": "not-for-presentation",
                    }
                ]
            },
        )

    def test_aggregate_presentation_maps_each_domain_individually(self) -> None:
        presentation = build_aggregate_presentation_data(
            self.aggregate(
                self.account_result(),
                self.transaction_result(),
                self.service_request_result(),
            )
        )

        self.assertIsInstance(presentation, AggregatePresentationData)
        self.assertEqual(
            [result.domain for result in presentation.results],
            ["accounts", "transactions", "service_requests"],
        )
        self.assertIsInstance(presentation.results[0].data, AccountsPresentationData)
        self.assertIsInstance(
            presentation.results[1].data,
            TransactionsPresentationData,
        )
        self.assertIsInstance(
            presentation.results[2].data,
            ServiceRequestsPresentationData,
        )

    def test_aggregate_presentation_supports_two_domains(self) -> None:
        presentation = build_aggregate_presentation_data(
            self.aggregate(
                self.account_result(),
                self.transaction_result(),
            )
        )

        self.assertEqual(
            [result.domain for result in presentation.results],
            ["accounts", "transactions"],
        )

    def test_aggregate_presentation_preserves_canonical_result_order(self) -> None:
        presentation = build_aggregate_presentation_data(
            self.aggregate(
                self.account_result(),
                self.transaction_result(),
                self.service_request_result(),
            )
        )

        self.assertEqual(
            [result.domain for result in presentation.results],
            ["accounts", "transactions", "service_requests"],
        )

    def test_noncanonical_coordinator_order_fails_closed(self) -> None:
        aggregate = self.aggregate(
            self.service_request_result(),
            self.account_result(),
            self.transaction_result(),
        )

        with self.assertRaises(PresentationDataError):
            build_aggregate_presentation_data(aggregate)

    def test_empty_results_are_distinct_from_failed_results(self) -> None:
        empty = DomainExecutionResult(
            domain="accounts",
            status="empty",
            data={"result": []},
        )
        failed = DomainExecutionResult(
            domain="transactions",
            status="failed",
            data=None,
            failure_code="operation_failed",
        )

        presentation = build_aggregate_presentation_data(
            self.aggregate(
                empty,
                failed,
                status="partial_failure",
            )
        )

        self.assertEqual(
            [result.status for result in presentation.results],
            ["empty", "failed"],
        )
        self.assertEqual(
            presentation.results[0].data,
            AccountsPresentationData(accounts=[]),
        )
        self.assertIsNone(presentation.results[1].data)
        self.assertEqual(
            presentation.results[1].failure_code,
            "operation_failed",
        )

    def test_total_failure_has_only_safe_failure_code(self) -> None:
        aggregate = self.aggregate(
            DomainExecutionResult(
                domain="accounts",
                status="failed",
                data=None,
                failure_code="operation_failed",
            ),
            status="failed",
        )

        presentation = build_aggregate_presentation_data(aggregate)

        self.assertTrue(
            all(result.data is None for result in presentation.results)
        )
        self.assertNotIn("exception", repr(presentation).lower())

    def test_aggregate_sanitization_removes_internal_fields(self) -> None:
        presentation = build_aggregate_presentation_data(
            self.aggregate(
                self.account_result(),
                self.transaction_result(),
                self.service_request_result(),
            )
        )
        serialized = presentation.model_dump_json(exclude_none=True)

        for forbidden in (
            "customer_id",
            "INTERNAL_ACCOUNT",
            "SYNTHETIC_ACCOUNT_NUMBER",
            "INTERNAL_TRANSACTION",
            "INTERNAL_REQUEST",
            "credential_hash",
            "synthetic-password",
            "SELECT *",
            '"id"',
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("Synthetic Cafe", serialized)

    def test_duplicate_and_unsupported_domains_are_rejected(self) -> None:
        duplicate = self.aggregate(
            self.account_result(),
            self.account_result(),
            status="succeeded",
        )
        unsupported = DomainExecutionResult(
            domain="payments",  # type: ignore[arg-type]
            status="failed",
            data=None,
            failure_code="operation_failed",
        )

        for aggregate in (
            duplicate,
            self.aggregate(unsupported, status="failed"),
        ):
            with self.subTest(results=aggregate.results):
                with self.assertRaises(PresentationDataError):
                    build_aggregate_presentation_data(aggregate)

    def test_inconsistent_status_and_data_combinations_are_rejected(self) -> None:
        invalid_aggregates = (
            self.aggregate(
                DomainExecutionResult(
                    domain="accounts",
                    status="failed",
                    data={"result": []},
                    failure_code="operation_failed",
                ),
                status="failed",
            ),
            self.aggregate(
                DomainExecutionResult(
                    domain="accounts",
                    status="empty",
                    data={"result": [{"balance": "1.00"}]},
                ),
            ),
            self.aggregate(
                DomainExecutionResult(
                    domain="accounts",
                    status="succeeded",
                    data={"result": []},
                ),
            ),
            self.aggregate(
                self.account_result(),
                status="failed",
            ),
            self.aggregate(
                outcome="unknown",
                status="not_dispatched",
                # A classifier outcome cannot carry executable result data.
                *[self.account_result()],
            ),
        )

        for aggregate in invalid_aggregates:
            with self.subTest(aggregate=aggregate):
                with self.assertRaises(PresentationDataError):
                    build_aggregate_presentation_data(aggregate)

    def test_non_supported_aggregate_with_no_results_is_valid(self) -> None:
        presentation = build_aggregate_presentation_data(
            self.aggregate(
                outcome="unsupported",
                status="not_dispatched",
            )
        )

        self.assertEqual(presentation.outcome, "unsupported")
        self.assertEqual(presentation.results, [])

    def test_raw_orm_rows_and_malformed_result_shapes_fail_closed(self) -> None:
        malformed_results = (
            {"result": [SimpleNamespace(balance="10.00")]},
            {"result": "not-a-list"},
            {"unexpected": []},
        )

        for data in malformed_results:
            aggregate = self.aggregate(
                DomainExecutionResult(
                    domain="accounts",
                    status="succeeded",
                    data=data,  # type: ignore[arg-type]
                )
            )
            with self.subTest(data=data):
                with self.assertRaises(PresentationDataError):
                    build_aggregate_presentation_data(aggregate)

    def test_raw_failure_message_is_not_accepted_as_failure_code(self) -> None:
        aggregate = self.aggregate(
            DomainExecutionResult(
                domain="accounts",
                status="failed",
                data=None,
                failure_code="SQL credentials traceback",  # type: ignore[arg-type]
            ),
            status="failed",
        )

        with self.assertRaises(PresentationDataError):
            build_aggregate_presentation_data(aggregate)

    async def test_supplied_balance_is_correctly_represented(self) -> None:
        data = build_presentation_data(
            "accounts",
            {
                "result": [
                    {
                        "account_id": "internal-account-id",
                        "account_number": "4111111111111111",
                        "account_type": "SAVINGS",
                        "balance": "52340.00",
                        "currency": "INR",
                        "status": "ACTIVE",
                    }
                ]
            },
        )
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={"answer": "Your savings balance is INR 52340.00."}
        )

        answer = await self.generator.generate(data)

        self.assertEqual(answer, "Your savings balance is INR 52340.00.")
        request = self.client.responses.parse.await_args.kwargs
        self.assertIn('"balance":"52340.00"', request["input"])
        self.assertIn('"currency":"INR"', request["input"])

    async def test_transaction_information_is_correctly_represented(self) -> None:
        data = build_presentation_data(
            "transactions",
            {
                "result": [
                    {
                        "transaction_id": "internal-txn-id",
                        "transaction_type": "DEBIT",
                        "amount": "125.00",
                        "currency": "INR",
                        "merchant": "Example Store",
                        "category": "Shopping",
                        "transaction_date": "2026-10-08T12:00:00+00:00",
                        "status": "COMPLETED",
                        "description": "Card purchase",
                    }
                ]
            },
        )
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={
                "answer": "A completed debit of 125.00 at Example Store was recorded."
            }
        )

        answer = await self.generator.generate(data)

        request = self.client.responses.parse.await_args.kwargs
        self.assertEqual(
            answer,
            "A completed debit of 125.00 at Example Store was recorded.",
        )
        for fact in (
            "125.00",
            "Example Store",
            "Shopping",
            "2026-10-08T12:00:00+00:00",
        ):
            self.assertIn(fact, request["input"])
        self.assertNotIn("internal-txn-id", request["input"])

    async def test_unneeded_ids_and_credentials_are_removed_before_model_input(self) -> None:
        jwt_token = create_access_token(71)
        data = build_presentation_data(
            "accounts",
            {
                "result": [
                    {
                        "customer_id": 1,
                        "account_id": "database-id",
                        "account_number": "masked-or-sensitive-number",
                        "account_type": "SAVINGS",
                        "balance": "10.00",
                        "currency": "INR",
                        "status": "ACTIVE",
                        "password": "password-secret",
                        "credential_hash": "credential-secret",
                        "sql": "SELECT * FROM accounts",
                        "jwt": jwt_token,
                        "access_token": "token-secret",
                    }
                ]
            },
        )
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={"answer": "Your savings balance is INR 10.00."}
        )

        await self.generator.generate(data)

        request = self.client.responses.parse.await_args.kwargs
        model_input = request["input"]
        for forbidden in (
            "customer_id",
            "database-id",
            "masked-or-sensitive-number",
            "password-secret",
            "credential-secret",
            "SELECT *",
            jwt_token,
            "token-secret",
        ):
            self.assertNotIn(forbidden, model_input)
        self.assertNotIn("JWT", request["instructions"])
        self.assertNotIn("customer_id", request["instructions"])

    async def test_raw_orm_objects_are_rejected_before_openai_call(self) -> None:
        raw_account = SimpleNamespace(
            account_type="SAVINGS",
            balance="10.00",
            currency="INR",
            status="ACTIVE",
        )
        with self.assertRaises(PresentationDataError):
            build_presentation_data("accounts", {"result": [raw_account]})

        with self.assertRaises(PresentationDataError):
            await self.generator.generate(raw_account)  # type: ignore[arg-type]

        self.client.responses.parse.assert_not_awaited()

    async def test_empty_data_is_explicitly_marked_insufficient_by_response(self) -> None:
        data = build_presentation_data("accounts", {"result": []})
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={
                "answer": "The available information is insufficient to answer."
            }
        )

        answer = await self.generator.generate(data)

        self.assertEqual(
            answer,
            "The available information is insufficient to answer.",
        )
        self.assertIn("using only the supplied", RESPONSE_INSTRUCTIONS)
        self.assertIn("Do not invent", RESPONSE_INSTRUCTIONS)
        self.assertIn("available information is insufficient", RESPONSE_INSTRUCTIONS)
        self.assertIn('"accounts":[]', self.client.responses.parse.await_args.kwargs["input"])

    async def test_serialized_account_list_is_rendered_from_validated_data(self) -> None:
        data = build_presentation_data(
            "accounts",
            {
                "result": [
                    {
                        "account_type": "SAVINGS",
                        "balance": "52340.00",
                        "currency": "INR",
                        "status": "ACTIVE",
                    },
                    {
                        "account_type": "SAVINGS",
                        "balance": "25000.00",
                        "currency": "INR",
                        "status": "ACTIVE",
                    },
                ]
            },
        )
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={
                "answer": (
                    '["SAVINGS, 52340.00 INR, ACTIVE", '
                    '"SAVINGS, 25000.00 INR, ACTIVE"]'
                )
            }
        )

        answer = await self.generator.generate(data)

        self.assertEqual(
            answer,
            "Your current account balances are: Savings account: INR 52340.00 "
            "(active); Savings account: INR 25000.00 (active).",
        )
        self.assertNotIn("[", answer)

    async def test_serialized_non_account_result_uses_trusted_fallback(self) -> None:
        data = build_presentation_data(
            "transactions",
            {
                "result": [
                    {
                        "transaction_type": "DEBIT",
                        "amount": "125.00",
                        "currency": "INR",
                        "merchant": "Example Store",
                        "category": "Shopping",
                        "transaction_date": "2026-10-08T12:00:00+00:00",
                        "status": "COMPLETED",
                        "description": "Card purchase",
                    }
                ]
            },
        )
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={"answer": '["DEBIT, 125.00, Example Store"]'}
        )

        answer = await self.generator.generate(data)

        self.assertEqual(
            answer,
            "Recent transactions: Debit INR 125.00 (Example Store, 2026-10-08).",
        )

    async def test_currency_symbol_hallucination_uses_trusted_fallback(self) -> None:
        data = build_presentation_data(
            "transactions",
            {
                "result": [
                    {
                        "transaction_type": "DEBIT",
                        "amount": "125.00",
                        "currency": "INR",
                        "merchant": "Example Store",
                        "category": "Shopping",
                        "transaction_date": "2026-10-08T12:00:00+00:00",
                        "status": "COMPLETED",
                        "description": "Card purchase",
                    }
                ]
            },
        )
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={"answer": "A debit of ₹125.00 was recorded."}
        )

        answer = await self.generator.generate(data)

        self.assertEqual(
            answer,
            "Recent transactions: Debit INR 125.00 (Example Store, 2026-10-08).",
        )

    async def test_aggregate_results_are_validated_for_openai_generation(self) -> None:
        data = build_aggregate_presentation_data(
            self.aggregate(
                self.account_result(),
                DomainExecutionResult(
                    domain="transactions",
                    status="failed",
                    data=None,
                    failure_code="operation_failed",
                ),
                status="partial_failure",
            )
        )
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={
                "answer": "Your account was retrieved, but transactions are unavailable."
            }
        )

        answer = await self.generator.generate(data)

        self.assertIn("transactions are unavailable", answer)
        request = self.client.responses.parse.await_args.kwargs
        self.assertIn('"status":"partial_failure"', request["input"])
        self.assertIn('"failure_code":"operation_failed"', request["input"])

    async def test_malformed_structured_output_fails_safely(self) -> None:
        data = build_presentation_data("accounts", {"result": []})
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={
                "answer": "Your balance is 999.",
                "customer_id": 1,
            }
        )

        with self.assertRaises(ResponseGenerationError):
            await self.generator.generate(data)

    async def test_openai_failure_fails_safely(self) -> None:
        data = build_presentation_data("accounts", {"result": []})
        self.client.responses.parse.side_effect = APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )

        with self.assertRaisesRegex(
            ResponseGenerationError,
            "Response generation is unavailable",
        ):
            await self.generator.generate(data)

    async def test_gemini_structured_output_can_generate_safe_response(self) -> None:
        data = build_presentation_data("accounts", {"result": []})
        gemini_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
        )
        gemini_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"answer":"The available information is '
                                'insufficient to answer."}'
                            )
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=8),
            )
        )
        self.generator = ResponseGenerator(
            GeminiStructuredOutputClient(gemini_client)
        )

        with unittest.mock.patch.object(settings, "GEMINI_MODEL", "gemini-test"):
            answer = await self.generator.generate(data)

        self.assertEqual(
            answer,
            "The available information is insufficient to answer.",
        )
        request = gemini_client.chat.completions.create.await_args.kwargs
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertIn("using only the supplied", request["messages"][0]["content"])

    async def test_ollama_structured_output_can_generate_safe_response(self) -> None:
        data = build_presentation_data("accounts", {"result": []})
        ollama_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
        )
        ollama_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"answer":"The available information is '
                                'insufficient to answer."}'
                            )
                        )
                    )
                ]
            )
        )
        self.generator = ResponseGenerator(
            OllamaStructuredOutputClient(ollama_client)
        )

        with unittest.mock.patch.object(settings, "OLLAMA_MODEL", "qwen3:1.7b"):
            answer = await self.generator.generate(data)

        self.assertEqual(
            answer,
            "The available information is insufficient to answer.",
        )
        self.assertEqual(
            ollama_client.chat.completions.create.await_args.kwargs["extra_body"],
            {"think": False},
        )

    async def test_response_generator_cannot_invoke_mcp(self) -> None:
        data = build_presentation_data("accounts", {"result": []})
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={"answer": "The available information is insufficient."}
        )

        with unittest.mock.patch.object(
            mcp_server,
            "call_tool",
            new=AsyncMock(),
        ) as call_tool:
            await self.generator.generate(data)

        call_tool.assert_not_awaited()

    def test_presentation_data_models_are_domain_typed(self) -> None:
        accounts = build_presentation_data("accounts", {"result": []})
        transactions = build_presentation_data("transactions", {"result": []})

        self.assertIsInstance(accounts, AccountsPresentationData)
        self.assertIsInstance(transactions, TransactionsPresentationData)


if __name__ == "__main__":
    unittest.main()
