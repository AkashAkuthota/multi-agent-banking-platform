"""Tests for transaction service ownership queries."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from app.models import Transaction
from app.services.transaction_service import (
    get_customer_transaction,
    get_customer_transaction_page,
    get_customer_transactions,
)


class TransactionServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_get_customer_transactions_uses_ownership_join_filter(self) -> None:
        transaction = self.make_transaction()
        session = AsyncMock()
        result = Mock()
        result.all.return_value = [transaction]
        session.scalars.return_value = result

        transactions = await get_customer_transactions(session, 42)

        self.assertEqual(transactions, [transaction])
        statement = session.scalars.call_args.args[0]
        sql = str(statement.compile())
        self.assertIn("JOIN accounts", sql)
        self.assertIn("accounts.customer_id", sql)
        self.assertEqual(statement.compile().params["customer_id_1"], 42)
        self.assertIn("LIMIT", sql)

    async def test_transaction_page_uses_limit_plus_one_and_returns_cursor(self) -> None:
        transactions = [self.make_transaction() for _ in range(3)]
        session = AsyncMock()
        result = Mock()
        result.all.return_value = transactions
        session.scalars.return_value = result

        page = await get_customer_transaction_page(
            session,
            42,
            limit=2,
            offset=4,
        )

        self.assertEqual(page.transactions, transactions[:2])
        self.assertEqual(page.next_offset, 6)
        statement = session.scalars.call_args.args[0]
        sql = str(statement.compile())
        self.assertIn("LIMIT", sql)
        self.assertIn("OFFSET", sql)

    async def test_transaction_page_rejects_invalid_bounds_before_querying(self) -> None:
        session = AsyncMock()

        with self.assertRaisesRegex(ValueError, "page size"):
            await get_customer_transaction_page(session, 42, limit=101)
        with self.assertRaisesRegex(ValueError, "offset"):
            await get_customer_transaction_page(session, 42, offset=-1)

        session.scalars.assert_not_awaited()

    async def test_get_customer_transaction_uses_id_and_ownership_filter(self) -> None:
        transaction = self.make_transaction()
        session = AsyncMock()
        session.scalar.return_value = transaction

        result = await get_customer_transaction(session, 42, "TXN_009")

        self.assertIs(result, transaction)
        statement = session.scalar.call_args.args[0]
        sql = str(statement.compile())
        params = statement.compile().params
        self.assertIn("JOIN accounts", sql)
        self.assertIn("transactions.transaction_id", sql)
        self.assertIn("accounts.customer_id", sql)
        self.assertEqual(params["transaction_id_1"], "TXN_009")
        self.assertEqual(params["customer_id_1"], 42)

    async def test_get_customer_transaction_returns_none_for_non_owner(self) -> None:
        session = AsyncMock()
        session.scalar.return_value = None

        result = await get_customer_transaction(session, 99, "TXN_009")

        self.assertIsNone(result)
        params = session.scalar.call_args.args[0].compile().params
        self.assertEqual(params["transaction_id_1"], "TXN_009")
        self.assertEqual(params["customer_id_1"], 99)


if __name__ == "__main__":
    unittest.main()
