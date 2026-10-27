"""Tests for synthetic intent evaluation metrics."""

import unittest

from app.evaluation import INTENT_CASES, evaluate_intents
from app.llm.schemas import Intent


class IntentEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_perfect_classifier_scores_all_synthetic_cases(self) -> None:
        class PerfectClassifier:
            async def classify(self, message: str) -> Intent:
                return next(
                    case.expected
                    for case in INTENT_CASES
                    if case.message == message
                )

        summary = await evaluate_intents(PerfectClassifier())

        self.assertEqual(summary.case_count, len(INTENT_CASES))
        self.assertEqual(summary.correct_count, len(INTENT_CASES))
        self.assertEqual(summary.accuracy, 1.0)
        self.assertEqual(summary.safety_violation_count, 0)

    async def test_unsupported_case_misclassified_as_supported_is_safety_violation(
        self,
    ) -> None:
        unsupported_case = next(
            case
            for case in INTENT_CASES
            if case.case_id == "unsupported-external-transfer"
        )

        class UnsafeClassifier:
            async def classify(self, _message: str) -> Intent:
                return Intent(outcome="supported", domains=["accounts"])

        summary = await evaluate_intents(
            UnsafeClassifier(),
            (unsupported_case,),
        )

        self.assertEqual(summary.accuracy, 0.0)
        self.assertEqual(summary.safety_violation_count, 1)

    async def test_over_selected_read_domain_is_a_safety_violation(self) -> None:
        balance_case = next(
            case for case in INTENT_CASES if case.case_id == "current-account-balance"
        )

        class OverSelectingClassifier:
            async def classify(self, _message: str) -> Intent:
                return Intent(
                    outcome="supported",
                    domains=["accounts", "transactions"],
                )

        summary = await evaluate_intents(OverSelectingClassifier(), (balance_case,))

        self.assertEqual(summary.accuracy, 0.0)
        self.assertEqual(summary.safety_violation_count, 1)

    async def test_command_misrouted_as_read_is_a_safety_violation(self) -> None:
        command_case = next(
            case
            for case in INTENT_CASES
            if case.case_id == "daily-limit-missing-account"
        )

        class ReadRoutingClassifier:
            async def classify(self, _message: str) -> Intent:
                return Intent(outcome="supported", domains=["accounts"])

        summary = await evaluate_intents(ReadRoutingClassifier(), (command_case,))

        self.assertEqual(summary.accuracy, 0.0)
        self.assertEqual(summary.safety_violation_count, 1)

    async def test_evaluation_rejects_empty_case_sets(self) -> None:
        class UnusedClassifier:
            async def classify(self, _message: str) -> Intent:
                raise AssertionError("Classifier should not be called")

        with self.assertRaisesRegex(ValueError, "at least one case"):
            await evaluate_intents(UnusedClassifier(), ())
