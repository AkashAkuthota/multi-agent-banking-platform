"""Tests for the authenticated accounts endpoint."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.accounts import get_account, get_my_accounts
from app.models import Account


class AccountsEndpointTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_returns_only_accounts_owned_by_authenticated_customer(self) -> None:
        account = self.make_account()
        session = AsyncMock()
        with patch(
            "app.api.accounts.get_customer_accounts",
            new=AsyncMock(return_value=[account]),
        ) as service:
            response = await get_my_accounts(42, session)

        service.assert_awaited_once_with(session, 42)
        self.assertEqual(
            response,
            {
                "accounts": [
                    {
                        "account_id": "ACC_007",
                        "account_type": "SAVINGS",
                        "account_number_last4": "7890",
                        "balance": Decimal("1250.00"),
                        "currency": "INR",
                        "status": "ACTIVE",
                        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    }
                ]
            },
        )

    async def test_returns_404_when_customer_has_no_accounts(self) -> None:
        session = AsyncMock()
        with patch(
            "app.api.accounts.get_customer_accounts",
            new=AsyncMock(return_value=[]),
        ) as service:
            with self.assertRaises(HTTPException) as error:
                await get_my_accounts(42, session)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "No accounts found")
        service.assert_awaited_once_with(session, 42)

    async def test_get_account_returns_account_owned_by_customer(self) -> None:
        account = self.make_account()
        session = AsyncMock()
        with patch(
            "app.api.accounts.get_customer_account",
            new=AsyncMock(return_value=account),
        ) as service:
            response = await get_account("ACC_007", 42, session)

        service.assert_awaited_once_with(session, 42, "ACC_007")
        self.assertEqual(response["account_id"], "ACC_007")
        self.assertEqual(response["account_type"], "SAVINGS")
        self.assertEqual(response["account_number_last4"], "7890")
        self.assertNotIn("account_number", response)
        self.assertEqual(response["balance"], Decimal("1250.00"))
        self.assertEqual(response["currency"], "INR")
        self.assertEqual(response["status"], "ACTIVE")
        self.assertEqual(
            response["created_at"],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    async def test_get_account_returns_same_404_for_other_customer(self) -> None:
        session = AsyncMock()
        with patch(
            "app.api.accounts.get_customer_account",
            new=AsyncMock(return_value=None),
        ) as service:
            with self.assertRaises(HTTPException) as error:
                await get_account("ACC_007", 99, session)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "Account not found")
        service.assert_awaited_once_with(session, 99, "ACC_007")

    async def test_get_account_returns_404_when_account_does_not_exist(self) -> None:
        session = AsyncMock()
        with patch(
            "app.api.accounts.get_customer_account",
            new=AsyncMock(return_value=None),
        ) as service:
            with self.assertRaises(HTTPException) as error:
                await get_account("ACC_MISSING", 42, session)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "Account not found")
        service.assert_awaited_once_with(session, 42, "ACC_MISSING")


if __name__ == "__main__":
    unittest.main()
