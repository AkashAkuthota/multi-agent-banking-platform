"""Tests for transaction API behavior."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.transactions import get_my_transactions, get_transaction
from app.models import Transaction
from app.services.transaction_service import TransactionPage


class TransactionsEndpointTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_transaction() -> Transaction:
        return Transaction(
            id=9,
            transaction_id="TXN_009",
            account_id=7,
            transaction_type="DEBIT",
            amount=Decimal("125.00"),
            merchant="Example Store",
            category="Shopping",
            transaction_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
            status="COMPLETED",
            description="Card purchase",
        )

    async def test_list_endpoint_calls_service_and_formats_response(self) -> None:
        transaction = self.make_transaction()
        session = AsyncMock()
        with patch(
            "app.api.transactions.get_customer_transaction_page",
            new=AsyncMock(
                return_value=TransactionPage(
                    transactions=[transaction],
                    next_offset=25,
                )
            ),
        ) as service:
            response = await get_my_transactions(42, session)

        service.assert_awaited_once_with(session, 42, limit=25, offset=0)
        self.assertEqual(
            response,
            {
                "transactions": [
                    {
                        "transaction_id": "TXN_009",
                        "transaction_type": "DEBIT",
                        "amount": Decimal("125.00"),
                        "merchant": "Example Store",
                        "category": "Shopping",
                        "transaction_date": datetime(
                            2026, 1, 2, tzinfo=timezone.utc
                        ),
                        "status": "COMPLETED",
                        "description": "Card purchase",
                    }
                ],
                "limit": 25,
                "offset": 0,
                "next_offset": 25,
            },
        )

    async def test_detail_endpoint_calls_service_and_formats_response(self) -> None:
        transaction = self.make_transaction()
        session = AsyncMock()
        with patch(
            "app.api.transactions.get_customer_transaction",
            new=AsyncMock(return_value=transaction),
        ) as service:
            response = await get_transaction("TXN_009", 42, session)

        service.assert_awaited_once_with(session, 42, "TXN_009")
        self.assertEqual(response["transaction_id"], "TXN_009")
        self.assertEqual(response["transaction_type"], "DEBIT")
        self.assertEqual(response["amount"], Decimal("125.00"))

    async def test_detail_endpoint_preserves_404_for_missing_or_non_owned(self) -> None:
        session = AsyncMock()
        with patch(
            "app.api.transactions.get_customer_transaction",
            new=AsyncMock(return_value=None),
        ) as service:
            with self.assertRaises(HTTPException) as error:
                await get_transaction("TXN_009", 99, session)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "Transaction not found")
        service.assert_awaited_once_with(session, 99, "TXN_009")


if __name__ == "__main__":
    unittest.main()
