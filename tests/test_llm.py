"""Tests for safe structured intent classification and dispatch."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import httpx
from openai import AsyncOpenAI, APIConnectionError
from pydantic import SecretStr
from pydantic import ValidationError

from app.core.config import settings
from app.llm.client import (
    CLASSIFIER_INSTRUCTIONS,
    GEMINI_OPENAI_BASE_URL,
    GeminiStructuredOutputClient,
    GeminiStructuredOutputError,
    IntentClassificationError,
    OpenAIIntentProvider,
    OllamaFallbackStructuredOutputClient,
    OllamaStructuredOutputClient,
    OllamaStructuredOutputError,
    StructuredOutputError,
    StructuredIntentProvider,
    create_intent_provider,
    create_structured_output_client,
)
from app.llm.router import IntentRouter
from app.llm.schemas import Intent
from app.mcp.server import mcp_server


class IntentClassificationTests(unittest.IsolatedAsyncioTestCase):
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
        self.provider = OpenAIIntentProvider(client=self.client)

    async def classify_as(
        self,
        outcome: str,
        domains: list[str],
        message: str = "test request",
    ) -> Intent:
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={"outcome": outcome, "domains": domains}
        )
        return await self.provider.classify(message)

    async def test_classifies_each_supported_single_domain(self) -> None:
        cases = (
            ("accounts", "What is my account balance?"),
            ("transactions", "Show my recent transactions."),
            ("service_requests", "Show my open service requests."),
        )

        for domain, message in cases:
            with self.subTest(domain=domain):
                intent = await self.classify_as("supported", [domain], message)
                self.assertEqual(
                    intent,
                    Intent(outcome="supported", domains=[domain]),
                )

    async def test_classifies_genuine_two_domain_request(self) -> None:
        intent = await self.classify_as(
            "supported",
            ["accounts", "transactions"],
            "Show my balance and recent transactions.",
        )

        self.assertEqual(
            intent,
            Intent(outcome="supported", domains=["accounts", "transactions"]),
        )

    async def test_classifies_all_three_supported_domains(self) -> None:
        intent = await self.classify_as(
            "supported",
            ["accounts", "transactions", "service_requests"],
            "Show my accounts, transactions, and service requests.",
        )

        self.assertEqual(
            intent,
            Intent(
                outcome="supported",
                domains=["accounts", "transactions", "service_requests"],
            ),
        )

    async def test_supported_output_is_normalized_to_canonical_domain_order(
        self,
    ) -> None:
        intent = await self.classify_as(
            "supported",
            ["service_requests", "transactions", "accounts"],
            "Show all my banking information.",
        )

        self.assertEqual(
            intent.domains,
            ["accounts", "transactions", "service_requests"],
        )

    async def test_classifies_all_non_supported_outcomes(self) -> None:
        cases = (
            ("unknown", "Write a poem about mountains."),
            ("ambiguous", "Do something with my account."),
            ("unsupported", "Transfer money to another account."),
        )

        for outcome, message in cases:
            with self.subTest(outcome=outcome):
                intent = await self.classify_as(outcome, [], message)
                self.assertEqual(intent.outcome, outcome)
                self.assertEqual(intent.domains, [])

    async def test_classifier_can_select_requested_domain_not_background_mention(
        self,
    ) -> None:
        intent = await self.classify_as(
            "supported",
            ["accounts"],
            "I made a transaction yesterday. What is my balance?",
        )

        self.assertEqual(intent.domains, ["accounts"])

    async def test_supported_and_unsupported_tasks_produce_no_domains(self) -> None:
        intent = await self.classify_as(
            "unsupported",
            [],
            "What is my balance? Also tell me tomorrow's weather.",
        )

        self.assertEqual(intent.outcome, "unsupported")
        self.assertEqual(intent.domains, [])

    async def test_classifier_instructions_describe_task_semantics_and_json(
        self,
    ) -> None:
        self.assertIn("valid JSON", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("actually requests", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("mentioned as background", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("unsupported", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("ambiguous", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("unknown", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("internal_transfer", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("full proposal UUID", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("least-data domain selection", CLASSIFIER_INSTRUCTIONS)
        self.assertIn("What is my current balance?", CLASSIFIER_INSTRUCTIONS)
        self.assertIn('domains ["accounts"]', CLASSIFIER_INSTRUCTIONS)

    async def test_classifies_a_daily_limit_command_without_authorizing_it(self) -> None:
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={
                "outcome": "supported",
                "domains": [],
                "action": {
                    "kind": "daily_limit_change",
                    "account_id": "ACC_001",
                    "requested_limit": "20000.00",
                },
            }
        )

        intent = await self.provider.classify(
            "Increase the daily limit on ACC_001 to 20000 INR."
        )

        self.assertEqual(intent.outcome, "supported")
        self.assertEqual(intent.domains, [])
        self.assertEqual(intent.action.kind, "daily_limit_change")
        self.assertEqual(intent.action.account_id, "ACC_001")
        self.assertEqual(intent.action.requested_limit, "20000.00")
        self.assertNotIn('"oneOf"', repr(intent.model_json_schema()))

    async def test_confirmation_requires_a_valid_explicit_proposal_id(self) -> None:
        self.client.responses.parse.return_value = SimpleNamespace(
            output_parsed={
                "outcome": "supported",
                "domains": [],
                "action": {
                    "kind": "confirm",
                    "request_id": "not-a-uuid",
                },
            }
        )

        with self.assertRaises(IntentClassificationError):
            await self.provider.classify("Confirm proposal not-a-uuid.")

    async def test_gpt4_uses_responses_json_mode_and_validates_schema(self) -> None:
        self.client.responses.create = AsyncMock(
            return_value=SimpleNamespace(
                output_text=(
                    '{"outcome":"supported",'
                    '"domains":["accounts","transactions"]}'
                )
            )
        )

        with unittest.mock.patch.object(settings, "OPENAI_MODEL", "gpt-4"):
            intent = await self.provider.classify(
                "Show my balance and recent transactions."
            )

        self.assertEqual(
            intent,
            Intent(outcome="supported", domains=["accounts", "transactions"]),
        )
        kwargs = self.client.responses.create.await_args.kwargs
        self.assertEqual(kwargs["text"], {"format": {"type": "json_object"}})
        self.assertEqual(
            kwargs["input"],
            "Show my balance and recent transactions.",
        )
        self.assertIn("valid JSON", kwargs["instructions"])
        self.client.responses.parse.assert_not_awaited()

    async def test_gpt4_malformed_json_fails_without_text_fallback(self) -> None:
        self.client.responses.create = AsyncMock(
            return_value=SimpleNamespace(output_text="supported accounts")
        )

        with unittest.mock.patch.object(settings, "OPENAI_MODEL", "gpt-4"):
            with self.assertRaises(IntentClassificationError):
                await self.provider.classify("Show my accounts.")

        self.client.responses.parse.assert_not_awaited()

    async def test_invalid_schema_output_fails_classification_safely(self) -> None:
        invalid_results = (
            {"outcome": "supported", "domains": []},
            {"outcome": "unknown", "domains": ["accounts"]},
            {"outcome": "unsupported", "domains": ["transactions"]},
            {"outcome": "supported", "domains": ["accounts", "accounts"]},
            {"outcome": "supported", "domains": ["payments"]},
            {"outcome": "supported", "domains": ["get_my_accounts"]},
            {"outcome": "maybe", "domains": []},
            {"outcome": "supported", "domains": "accounts"},
            {"outcome": "supported", "domains": [42]},
            {"outcome": "supported", "domains": ["accounts"], "domain": "accounts"},
            {
                "outcome": "supported",
                "domains": ["accounts"],
                "action": {
                    "kind": "confirm",
                    "request_id": "66b74620-aefb-42ae-9c94-796a2120991c",
                },
            },
            {
                "outcome": "supported",
                "domains": [],
                "action": {
                    "kind": "daily_limit_change",
                    "account_id": "ACC_001",
                    "amount": "100.00",
                },
            },
            {"outcome": 1, "domains": []},
            {"outcome": "supported"},
        )

        for output in invalid_results:
            with self.subTest(output=output):
                self.client.responses.parse.return_value = SimpleNamespace(
                    output_parsed=output
                )
                with self.assertRaises(IntentClassificationError):
                    await self.provider.classify("Show my accounts.")

    async def test_extra_identity_sql_tool_and_authorization_fields_are_rejected(
        self,
    ) -> None:
        malicious_outputs = (
            {"customer_id": 1},
            {"sql": "SELECT * FROM accounts"},
            {"tool_name": "get_my_accounts"},
            {"arguments": {"account_id": "A-1"}},
            {"authorization": "approved"},
        )

        for extra in malicious_outputs:
            with self.subTest(extra=extra):
                self.client.responses.parse.return_value = SimpleNamespace(
                    output_parsed={
                        "outcome": "supported",
                        "domains": ["accounts"],
                        **extra,
                    }
                )
                with self.assertRaises(IntentClassificationError):
                    await self.provider.classify("Show my balance.")

    async def test_model_errors_are_wrapped_as_controlled_error(self) -> None:
        self.client.responses.parse.side_effect = APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )

        with self.assertRaisesRegex(
            IntentClassificationError,
            "Responses API is unavailable",
        ):
            await self.provider.classify("What's my account balance?")

    async def test_model_configuration_is_required(self) -> None:
        with unittest.mock.patch.object(settings, "OPENAI_MODEL", None):
            with self.assertRaisesRegex(
                IntentClassificationError,
                "OPENAI_MODEL is not configured",
            ):
                await self.provider.classify("What's my balance?")

    async def test_gemini_json_output_is_validated_as_intent(self) -> None:
        gemini_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
        )
        gemini_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"outcome":"supported",'
                                '"domains":["accounts"]}'
                            )
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
            )
        )
        provider = OpenAIIntentProvider(
            responses_client=GeminiStructuredOutputClient(gemini_client)
        )

        with unittest.mock.patch.object(settings, "GEMINI_MODEL", "gemini-test"):
            intent = await provider.classify("What is my balance?")

        self.assertEqual(intent, Intent(outcome="supported", domains=["accounts"]))
        request = gemini_client.chat.completions.create.await_args.kwargs
        self.assertEqual(request["model"], "gemini-test")
        self.assertEqual(request["messages"][1]["content"], "What is my balance?")
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.client.responses.parse.assert_not_awaited()

    async def test_gemini_invalid_json_fails_closed(self) -> None:
        gemini_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
        )
        gemini_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="supported accounts")
                    )
                ]
            )
        )
        provider = OpenAIIntentProvider(
            responses_client=GeminiStructuredOutputClient(gemini_client)
        )

        with (
            unittest.mock.patch.object(settings, "GEMINI_MODEL", "gemini-test"),
            self.assertRaisesRegex(
                IntentClassificationError,
                "Gemini returned invalid structured output",
            ),
        ):
            await provider.classify("What is my balance?")

    async def test_gemini_connection_failure_is_controlled(self) -> None:
        gemini_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
        )
        gemini_client.chat.completions.create = AsyncMock(
            side_effect=APIConnectionError(
                request=httpx.Request(
                    "POST",
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                )
            )
        )
        provider = OpenAIIntentProvider(
            responses_client=GeminiStructuredOutputClient(gemini_client)
        )

        with (
            unittest.mock.patch.object(settings, "GEMINI_MODEL", "gemini-test"),
            self.assertRaisesRegex(
                IntentClassificationError,
                "Gemini API is unavailable",
            ),
        ):
            await provider.classify("What is my balance?")

    async def test_gemini_configuration_requires_model_and_api_key(self) -> None:
        with (
            unittest.mock.patch.object(settings, "GEMINI_MODEL", None),
            self.assertRaisesRegex(
                GeminiStructuredOutputError,
                "GEMINI_MODEL is not configured",
            ),
        ):
            await GeminiStructuredOutputClient(self.client).parse(
                instructions=CLASSIFIER_INSTRUCTIONS,
                input_text="Show my balance",
                response_model=Intent,
            )

        with (
            unittest.mock.patch.object(settings, "GEMINI_MODEL", "gemini-test"),
            unittest.mock.patch.object(settings, "GEMINI_API_KEY", None),
            self.assertRaisesRegex(
                GeminiStructuredOutputError,
                "GEMINI_API_KEY is not configured",
            ),
        ):
            GeminiStructuredOutputClient._create_client()

    async def test_provider_factory_selects_gemini_without_changing_openai_default(
        self,
    ) -> None:
        with unittest.mock.patch.object(settings, "LLM_PROVIDER", "gemini"):
            self.assertIsInstance(
                create_structured_output_client(),
                OllamaFallbackStructuredOutputClient,
            )
            self.assertIsInstance(create_intent_provider(), StructuredIntentProvider)

        with unittest.mock.patch.object(settings, "LLM_PROVIDER", "openai"):
            self.assertIsInstance(
                create_structured_output_client(),
                OllamaFallbackStructuredOutputClient,
            )

        with unittest.mock.patch.object(settings, "LLM_PROVIDER", "ollama"):
            self.assertIsInstance(
                create_structured_output_client(),
                OllamaStructuredOutputClient,
            )

    async def test_ollama_fallback_retries_after_primary_provider_failure(
        self,
    ) -> None:
        primary_client = AsyncMock()
        primary_client.parse.side_effect = StructuredOutputError(
            "Primary provider unavailable"
        )
        fallback_client = AsyncMock()
        expected = Intent(outcome="supported", domains=["accounts"])
        fallback_client.parse.return_value = expected
        client = OllamaFallbackStructuredOutputClient(
            primary_client,
            primary_provider="gemini",
            fallback_client=fallback_client,
        )

        result = await client.parse(
            instructions=CLASSIFIER_INSTRUCTIONS,
            input_text="What is my balance?",
            response_model=Intent,
        )

        self.assertEqual(result, expected)
        primary_client.parse.assert_awaited_once()
        fallback_client.parse.assert_awaited_once_with(
            instructions=CLASSIFIER_INSTRUCTIONS,
            input_text="What is my balance?",
            response_model=Intent,
        )

    async def test_ollama_fallback_is_not_used_when_primary_succeeds(self) -> None:
        primary_client = AsyncMock()
        expected = Intent(outcome="supported", domains=["accounts"])
        primary_client.parse.return_value = expected
        fallback_client = AsyncMock()
        client = OllamaFallbackStructuredOutputClient(
            primary_client,
            primary_provider="openai",
            fallback_client=fallback_client,
        )

        result = await client.parse(
            instructions=CLASSIFIER_INSTRUCTIONS,
            input_text="What is my balance?",
            response_model=Intent,
        )

        self.assertEqual(result, expected)
        primary_client.parse.assert_awaited_once()
        fallback_client.parse.assert_not_awaited()

    async def test_ollama_fallback_does_not_mask_fallback_failure(self) -> None:
        primary_client = AsyncMock()
        primary_client.parse.side_effect = StructuredOutputError(
            "Primary provider unavailable"
        )
        fallback_client = AsyncMock()
        fallback_client.parse.side_effect = OllamaStructuredOutputError(
            "Local Ollama API is unavailable"
        )
        client = OllamaFallbackStructuredOutputClient(
            primary_client,
            primary_provider="openai",
            fallback_client=fallback_client,
        )

        with self.assertRaisesRegex(
            OllamaStructuredOutputError,
            "Local Ollama API is unavailable",
        ):
            await client.parse(
                instructions=CLASSIFIER_INSTRUCTIONS,
                input_text="What is my balance?",
                response_model=Intent,
            )

    async def test_gemini_client_uses_google_openai_compatible_endpoint(self) -> None:
        api_key = "synthetic-gemini-key"
        with (
            unittest.mock.patch.object(
                settings,
                "GEMINI_API_KEY",
                SecretStr(api_key),
            ),
            unittest.mock.patch(
                "app.llm.client.AsyncOpenAI"
            ) as openai_client_factory,
        ):
            GeminiStructuredOutputClient._create_client()

        openai_client_factory.assert_called_once_with(
            api_key=api_key,
            base_url=GEMINI_OPENAI_BASE_URL,
        )

    async def test_ollama_json_output_is_validated_and_thinking_is_disabled(
        self,
    ) -> None:
        ollama_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
        )
        ollama_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"outcome":"supported",'
                                '"domains":["accounts"]}'
                            )
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=30, completion_tokens=12),
            )
        )
        provider = OpenAIIntentProvider(
            responses_client=OllamaStructuredOutputClient(ollama_client)
        )

        with unittest.mock.patch.object(settings, "OLLAMA_MODEL", "qwen3:1.7b"):
            intent = await provider.classify("What is my balance?")

        self.assertEqual(intent, Intent(outcome="supported", domains=["accounts"]))
        request = ollama_client.chat.completions.create.await_args.kwargs
        self.assertEqual(request["model"], "qwen3:1.7b")
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertEqual(
            request["response_format"]["json_schema"]["schema"],
            Intent.model_json_schema(),
        )
        self.assertEqual(request["extra_body"], {"think": False})
        self.assertIn("/no_think", request["messages"][0]["content"])

    async def test_ollama_empty_or_invalid_output_fails_closed(self) -> None:
        for content in ("", "not-json", '{"outcome":"supported","domains":[]}'):
            with self.subTest(content=content):
                ollama_client = SimpleNamespace(
                    chat=SimpleNamespace(
                        completions=SimpleNamespace(create=AsyncMock())
                    )
                )
                ollama_client.chat.completions.create = AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content=content)
                            )
                        ]
                    )
                )
                provider = OpenAIIntentProvider(
                    responses_client=OllamaStructuredOutputClient(ollama_client)
                )
                with (
                    unittest.mock.patch.object(
                        settings,
                        "OLLAMA_MODEL",
                        "qwen3:1.7b",
                    ),
                    self.assertRaises(IntentClassificationError),
                ):
                    await provider.classify("What is my balance?")

    async def test_ollama_connection_failure_is_controlled(self) -> None:
        ollama_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
        )
        ollama_client.chat.completions.create = AsyncMock(
            side_effect=APIConnectionError(
                request=httpx.Request(
                    "POST",
                    "http://127.0.0.1:11434/v1/chat/completions",
                )
            )
        )
        provider = OpenAIIntentProvider(
            responses_client=OllamaStructuredOutputClient(ollama_client)
        )
        with (
            unittest.mock.patch.object(settings, "OLLAMA_MODEL", "qwen3:1.7b"),
            self.assertRaisesRegex(
                IntentClassificationError,
                "Local Ollama API is unavailable",
            ),
        ):
            await provider.classify("What is my balance?")

    async def test_ollama_client_uses_local_endpoint_without_secret(self) -> None:
        with (
            unittest.mock.patch.object(
                settings,
                "OLLAMA_BASE_URL",
                "http://127.0.0.1:11434/v1/",
            ),
            unittest.mock.patch(
                "app.llm.client.AsyncOpenAI"
            ) as openai_client_factory,
        ):
            OllamaStructuredOutputClient._create_client()

        openai_client_factory.assert_called_once_with(
            api_key="ollama",
            base_url="http://127.0.0.1:11434/v1/",
        )


class IntentRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_passes_only_message_to_classifier(self) -> None:
        provider = AsyncMock()
        expected = Intent(
            outcome="supported",
            domains=["transactions", "accounts"],
        )
        provider.classify.return_value = expected
        router = IntentRouter(provider)

        intent = await router.classify("Show my account and transaction details.")

        self.assertEqual(intent, expected)
        provider.classify.assert_awaited_once_with(
            "Show my account and transaction details."
        )
        self.assertNotIn("customer_id", repr(provider.classify.await_args))

    async def test_router_does_not_invoke_mcp(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        router = IntentRouter(provider)

        with unittest.mock.patch.object(
            mcp_server,
            "call_tool",
            new=AsyncMock(),
        ) as call_tool:
            intent = await router.classify("Show my accounts")

        self.assertEqual(intent.outcome, "supported")
        call_tool.assert_not_awaited()

    async def test_daily_limit_command_cannot_be_misrouted_as_an_account_read(
        self,
    ) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        router = IntentRouter(provider)

        intent = await router.classify(
            "Increase my current daily limit to 20000"
        )

        self.assertEqual(intent.outcome, "supported")
        self.assertEqual(intent.domains, [])
        self.assertIsNotNone(intent.action)
        self.assertEqual(intent.action.kind, "daily_limit_change")
        self.assertIsNone(intent.action.account_id)
        self.assertEqual(intent.action.requested_limit, "20000")

    async def test_daily_limit_guard_preserves_an_explicit_account_id(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        router = IntentRouter(provider)

        intent = await router.classify(
            "Set the daily limit on ACC_001 to INR 20000.00"
        )

        self.assertIsNotNone(intent.action)
        self.assertEqual(intent.action.account_id, "ACC_001")
        self.assertEqual(intent.action.requested_limit, "20000.00")

    async def test_daily_limit_read_is_not_treated_as_a_change_command(self) -> None:
        provider = AsyncMock()
        expected = Intent(outcome="supported", domains=["accounts"])
        provider.classify.return_value = expected
        router = IntentRouter(provider)

        intent = await router.classify("Show my current daily limit")

        self.assertEqual(intent, expected)

    async def test_proposal_decision_cannot_be_misrouted_as_a_read(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        router = IntentRouter(provider)
        request_id = "66b74620-aefb-42ae-9c94-796a2120991c"

        intent = await router.classify(f"Confirm proposal {request_id}")

        self.assertIsNotNone(intent.action)
        self.assertEqual(intent.action.kind, "confirm")
        self.assertEqual(intent.action.request_id, request_id)
        self.assertEqual(intent.domains, [])
        provider.classify.assert_not_awaited()

    async def test_explicit_internal_transfer_cannot_be_misrouted_as_history(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["transactions"],
        )
        router = IntentRouter(provider)

        intent = await router.classify(
            "Transfer 100 INR from ACC_001 to ACC_DEMO_002"
        )

        self.assertIsNotNone(intent.action)
        self.assertEqual(intent.action.kind, "internal_transfer")
        self.assertEqual(intent.action.source_account_id, "ACC_001")
        self.assertEqual(intent.action.destination_account_id, "ACC_DEMO_002")
        self.assertEqual(intent.action.amount, "100")

    async def test_service_request_cannot_be_misrouted_as_request_history(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["service_requests"],
        )
        router = IntentRouter(provider)

        intent = await router.classify("Request a replacement card")

        self.assertIsNotNone(intent.action)
        self.assertEqual(intent.action.kind, "service_request")
        self.assertEqual(intent.action.request_type, "CARD_REPLACEMENT")


class IntentSchemaTests(unittest.TestCase):
    def test_schema_rejects_customer_identity(self) -> None:
        with self.assertRaises(ValidationError):
            Intent.model_validate(
                {
                    "outcome": "supported",
                    "domains": ["accounts"],
                    "customer_id": 1,
                }
            )

    def test_cross_field_and_duplicate_rules_are_enforced(self) -> None:
        invalid_results = (
            {"outcome": "supported", "domains": []},
            {"outcome": "unknown", "domains": ["accounts"]},
            {"outcome": "ambiguous", "domains": ["transactions"]},
            {
                "outcome": "supported",
                "domains": ["accounts", "accounts"],
            },
        )

        for result in invalid_results:
            with self.subTest(result=result):
                with self.assertRaises(ValidationError):
                    Intent.model_validate(result)


if __name__ == "__main__":
    unittest.main()
