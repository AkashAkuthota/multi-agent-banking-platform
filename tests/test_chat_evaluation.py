"""Tests for the synthetic end-to-end chat evaluation metrics."""

import unittest

from app.evaluation import CHAT_CASES, evaluate_chat_responses
from app.schemas.chat import ChatAccountOption, ChatResponse


class ChatEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_evaluation_scores_reviewed_safe_responses(self) -> None:
        responses = {
            "What is my current balance?": ChatResponse(
                domain="accounts",
                answer=(
                    "Savings account: INR 75,000.00 active. "
                    "Current account: INR 25,000.00 active."
                ),
            ),
            "Show my recent transactions.": ChatResponse(
                domain="transactions",
                answer=(
                    "Debit INR 10,000.00 at Synthetic Utility. "
                    "Credit INR 85,000.00."
                ),
            ),
            "Show my service requests.": ChatResponse(
                domain="service_requests",
                answer="Your statement request was completed on 2026-01-04.",
            ),
            "Increase my daily limit to 50000.": ChatResponse(
                answer="Choose an account before I prepare a proposal.",
                clarification="daily_limit_details",
                account_options=[
                    ChatAccountOption(account_id="ACC_001", account_type="SAVINGS"),
                    ChatAccountOption(account_id="ACC_002", account_type="CURRENT"),
                ],
            ),
        }

        async def responder(message: str) -> ChatResponse:
            return responses[message]

        summary = await evaluate_chat_responses(responder)

        self.assertEqual(summary.case_count, len(CHAT_CASES))
        self.assertEqual(summary.passed_case_count, len(CHAT_CASES))
        self.assertEqual(summary.accuracy, 1.0)
        self.assertEqual(summary.relevance_violation_count, 0)
        self.assertEqual(summary.grounding_violation_count, 0)
        self.assertEqual(summary.safety_violation_count, 0)
        self.assertEqual(summary.availability_failure_count, 0)

    async def test_evaluation_detects_wrong_currency_and_privacy_leak(self) -> None:
        async def responder(message: str) -> ChatResponse:
            if message == "What is my current balance?":
                return ChatResponse(
                    domain="accounts",
                    answer=(
                        "Savings account: USD 75,000.00 active. "
                        "Current account: INR 25,000.00 active. "
                        "Account number 0000111122223333."
                    ),
                )
            raise AssertionError("Only the balance case should be evaluated")

        summary = await evaluate_chat_responses(responder, (CHAT_CASES[0],))

        self.assertEqual(summary.accuracy, 0.0)
        self.assertEqual(summary.grounding_violation_count, 2)

    async def test_evaluation_detects_command_that_skips_clarification(self) -> None:
        async def responder(_message: str) -> ChatResponse:
            return ChatResponse(domain="accounts", answer="Your accounts are active.")

        summary = await evaluate_chat_responses(responder, (CHAT_CASES[3],))

        self.assertEqual(summary.accuracy, 0.0)
        self.assertEqual(summary.relevance_violation_count, 1)
        self.assertEqual(summary.safety_violation_count, 1)

    async def test_evaluation_records_provider_availability_failures(self) -> None:
        async def unavailable(_message: str) -> ChatResponse:
            raise RuntimeError("Synthetic provider outage")

        summary = await evaluate_chat_responses(unavailable, (CHAT_CASES[0],))

        self.assertEqual(summary.accuracy, 0.0)
        self.assertEqual(summary.availability_failure_count, 1)
        self.assertEqual(summary.p95_latency_ms, 0.0)

    async def test_evaluation_rejects_empty_case_sets(self) -> None:
        async def unused(_message: str) -> ChatResponse:
            raise AssertionError("Responder should not be called")

        with self.assertRaisesRegex(ValueError, "at least one case"):
            await evaluate_chat_responses(unused, ())


if __name__ == "__main__":
    unittest.main()
