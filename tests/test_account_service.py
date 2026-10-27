"""Tests for account service ownership queries."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from app.models import Account
from app.services.account_service import (
    get_customer_account,
    get_customer_accounts,
)


class AccountServiceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_account(customer_id: int = 42) -> Account:
        return Account(
            id=7,
            account_id="ACC_007",
            customer_id=customer_id,
            account_type="SAVINGS",
            account_number="1234567890",
            balance=Decimal("1250.00"),
            currency="INR",
            status="ACTIVE",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    async def test_get_customer_accounts_filters_in_sql(self) -> None:
        account = self.make_account()
        session = AsyncMock()
        result = Mock()
        result.all.return_value = [account]
        session.scalars.return_value = result

        accounts = await get_customer_accounts(session, 42)

        self.assertEqual(accounts, [account])
        statement = session.scalars.call_args.args[0]
        self.assertEqual(statement.compile().params["customer_id_1"], 42)
        self.assertIn("accounts.customer_id", str(statement.compile()))

    async def test_get_customer_account_filters_id_and_owner_in_sql(self) -> None:
        account = self.make_account()
        session = AsyncMock()
        session.scalar.return_value = account

        result = await get_customer_account(session, 42, "ACC_007")

        self.assertIs(result, account)
        statement = session.scalar.call_args.args[0]
        params = statement.compile().params
        sql = str(statement.compile())
        self.assertEqual(params["account_id_1"], "ACC_007")
        self.assertEqual(params["customer_id_1"], 42)
        self.assertIn("accounts.account_id", sql)
        self.assertIn("accounts.customer_id", sql)

    async def test_get_customer_account_returns_none_for_non_owner(self) -> None:
        session = AsyncMock()
        session.scalar.return_value = None

        result = await get_customer_account(session, 99, "ACC_007")

        self.assertIsNone(result)
        params = session.scalar.call_args.args[0].compile().params
        self.assertEqual(params["account_id_1"], "ACC_007")
        self.assertEqual(params["customer_id_1"], 99)


if __name__ == "__main__":
    unittest.main()
