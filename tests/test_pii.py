"""Tests for targeted PII redaction at third-party LLM boundaries."""

import unittest
from unittest.mock import AsyncMock

from app.core.pii import account_number_last4, redact_sensitive_text
from app.llm.presentation import (
    TransactionsPresentationData,
    build_presentation_data,
)
from app.llm.router import IntentRouter
from app.llm.schemas import Intent


class PiiRedactionTests(unittest.IsolatedAsyncioTestCase):
    def test_returns_only_the_final_four_digits_for_account_display(self) -> None:
        self.assertEqual(account_number_last4("1234567890"), "7890")

    def test_redacts_email_phone_ssn_and_long_identifiers(self) -> None:
        text = (
            "Contact me at alex@example.com or +1 (415) 555-2671. "
            "SSN 123-45-6789; account 123456789012."
        )

        redacted = redact_sensitive_text(text)

        self.assertNotIn("alex@example.com", redacted)
        self.assertNotIn("415", redacted)
        self.assertNotIn("123-45-6789", redacted)
        self.assertNotIn("123456789012", redacted)
        self.assertEqual(redacted.count("[REDACTED]"), 4)

    def test_keeps_regular_currency_amounts_and_normal_banking_text(self) -> None:
        text = "My account balance is INR 52340.00 for the month."

        self.assertEqual(redact_sensitive_text(text), text)

    async def test_intent_router_redacts_message_before_third_party_call(self) -> None:
        provider = AsyncMock()
        provider.classify.return_value = Intent(
            outcome="supported",
            domains=["accounts"],
        )
        router = IntentRouter(provider)

        await router.classify("Show balance for alex@example.com, phone 4155552671")

        provider.classify.assert_awaited_once_with(
            "Show balance for [REDACTED], phone [REDACTED]"
        )

    def test_transaction_free_text_is_redacted_but_amount_is_preserved(self) -> None:
        presentation = build_presentation_data(
            "transactions",
            {
                "result": [
                    {
                        "transaction_type": "DEBIT",
                        "amount": "2500.00",
                        "currency": "INR",
                        "merchant": "alex@example.com",
                        "category": "Dining",
                        "transaction_date": "2026-10-08T12:00:00+00:00",
                        "status": "COMPLETED",
                        "description": "Call 4155552671 about this purchase",
                    }
                ]
            },
        )

        self.assertIsInstance(presentation, TransactionsPresentationData)
        transaction = presentation.transactions[0]
        self.assertEqual(transaction.merchant, "[REDACTED]")
        self.assertEqual(
            transaction.description,
            "Call [REDACTED] about this purchase",
        )
        self.assertEqual(transaction.amount, "2500.00")
        self.assertEqual(transaction.currency, "INR")

