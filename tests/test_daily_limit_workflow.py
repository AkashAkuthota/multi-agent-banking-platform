"""Tests for sandbox daily-limit policy and durable workflow services."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.models import (
    Account,
    DailyAccountLimit,
    DailyLimitAuditEvent,
    DailyLimitChangeRequest,
)
from app.services.daily_limit_service import (
    DailyLimitConflict,
    DailyLimitNotFound,
    cancel_daily_limit_change,
    confirm_daily_limit_change,
    propose_daily_limit_change,
)
from app.policies.daily_limit import DailyLimitPolicyError, validate_daily_limit


class DailyLimitPolicyTests(unittest.TestCase):
    def test_policy_accepts_inclusive_limits(self) -> None:
        self.assertEqual(validate_daily_limit(Decimal("10000.00")), Decimal("10000.00"))
        self.assertEqual(validate_daily_limit(Decimal("50000.00")), Decimal("50000.00"))

    def test_policy_rejects_out_of_range_and_over_precision_values(self) -> None:
        for amount in (
            Decimal("9999.99"),
            Decimal("50000.01"),
            Decimal("10000.001"),
            Decimal("NaN"),
        ):
            with self.subTest(amount=amount):
                with self.assertRaises(DailyLimitPolicyError):
                    validate_daily_limit(amount)


class DailyLimitWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_account() -> Account:
        return Account(
            id=5,
            account_id="ACC_005",
            customer_id=42,
            account_type="SAVINGS",
            account_number="5550012345",
            balance=Decimal("200.50"),
            currency="INR",
            status="ACTIVE",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    @staticmethod
    def make_limit() -> DailyAccountLimit:
        return DailyAccountLimit(
            account_id=5,
            daily_limit=Decimal("10000.00"),
            currency="INR",
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    @staticmethod
    def make_request(
        *,
        customer_id: int = 42,
        status: str = "PENDING",
        expires_at: datetime | None = None,
    ) -> DailyLimitChangeRequest:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return DailyLimitChangeRequest(
            request_id=uuid4(),
            idempotency_key="request-1",
            customer_id=customer_id,
            account_ref="ACC_005",
            account_id=5,
            previous_limit=Decimal("10000.00"),
            requested_limit=Decimal("20000.00"),
            currency="INR",
            status=status,
            created_at=now,
            expires_at=expires_at or now + timedelta(minutes=10),
        )

    async def test_proposal_persists_pending_request_and_audit_event(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session = AsyncMock()
        session.scalar = AsyncMock(
            side_effect=[self.make_account(), None, self.make_limit(), None]
        )
        session.add = Mock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = await propose_daily_limit_change(
            session,
            42,
            "ACC_005",
            Decimal("20000.00"),
            "request-1",
            now=now,
        )

        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["account_id"], "ACC_005")
        self.assertEqual(result["currency"], "INR")
        self.assertFalse(result["applied"])
        session.commit.assert_awaited_once()
        added = [call.args[0] for call in session.add.call_args_list]
        request = next(item for item in added if isinstance(item, DailyLimitChangeRequest))
        audit = next(item for item in added if isinstance(item, DailyLimitAuditEvent))
        self.assertEqual(request.customer_id, 42)
        self.assertEqual(request.expires_at, now + timedelta(minutes=10))
        self.assertEqual(audit.event_type, "PROPOSED")

    async def test_proposal_checks_account_ownership_in_query(self) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        with self.assertRaises(DailyLimitNotFound):
            await propose_daily_limit_change(
                session,
                42,
                "ACC_OTHER",
                Decimal("20000.00"),
                "request-1",
            )

        ownership_query = str(session.scalar.call_args.args[0].compile())
        self.assertIn("accounts.customer_id", ownership_query)
        self.assertIn("accounts.account_id", ownership_query)

    async def test_idempotent_proposal_retry_returns_original_request(self) -> None:
        existing = self.make_request(status="CONFIRMED")
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[self.make_account(), existing])

        result = await propose_daily_limit_change(
            session,
            42,
            "ACC_005",
            Decimal("20000.00"),
            "request-1",
        )

        self.assertEqual(result["request_id"], str(existing.request_id))
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertTrue(result["applied"])
        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_duplicate_open_proposal_is_rejected(self) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(
            side_effect=[
                self.make_account(),
                None,
                self.make_limit(),
                self.make_request(),
            ]
        )
        session.add = Mock()

        with self.assertRaises(DailyLimitConflict):
            await propose_daily_limit_change(
                session,
                42,
                "ACC_005",
                Decimal("25000.00"),
                "request-2",
                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

    async def test_expired_pending_request_is_logged_before_replacement(self) -> None:
        now = datetime(2026, 1, 1, 0, 11, tzinfo=timezone.utc)
        expired_request = self.make_request(
            expires_at=now - timedelta(seconds=1)
        )
        session = AsyncMock()
        session.scalar = AsyncMock(
            side_effect=[
                self.make_account(),
                None,
                self.make_limit(),
                expired_request,
            ]
        )
        session.add = Mock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = await propose_daily_limit_change(
            session,
            42,
            "ACC_005",
            Decimal("25000.00"),
            "request-2",
            now=now,
        )

        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(expired_request.status, "EXPIRED")
        self.assertEqual(session.flush.await_count, 2)
        self.assertEqual(
            [call.args[0].event_type for call in session.add.call_args_list if isinstance(call.args[0], DailyLimitAuditEvent)],
            ["EXPIRED", "PROPOSED"],
        )

    async def test_idempotency_unique_race_returns_the_committed_proposal(self) -> None:
        original = self.make_request()
        session = AsyncMock()
        session.scalar = AsyncMock(
            side_effect=[
                self.make_account(),
                None,
                self.make_limit(),
                None,
                original,
            ]
        )
        session.add = Mock()
        session.flush = AsyncMock()
        session.commit = AsyncMock(
            side_effect=IntegrityError("insert", {}, RuntimeError("unique conflict"))
        )
        session.rollback = AsyncMock()

        result = await propose_daily_limit_change(
            session,
            42,
            "ACC_005",
            Decimal("20000.00"),
            "request-1",
        )

        self.assertEqual(result["request_id"], str(original.request_id))
        session.rollback.assert_awaited_once()

    async def test_confirmation_applies_change_and_retry_is_safe(self) -> None:
        request = self.make_request()
        account = self.make_account()
        limit = self.make_limit()
        now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[request, account, request, limit])
        session.add = Mock()
        session.commit = AsyncMock()

        result = await confirm_daily_limit_change(
            session,
            42,
            request.request_id,
            True,
            now=now,
        )

        self.assertTrue(result["applied"])
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertEqual(limit.daily_limit, Decimal("20000.00"))
        self.assertEqual(request.confirmed_at, now)
        session.commit.assert_awaited_once()
        self.assertEqual(session.add.call_args.args[0].event_type, "CONFIRMED")

        retry_session = AsyncMock()
        retry_session.scalar = AsyncMock(side_effect=[request, account, request])
        retry_result = await confirm_daily_limit_change(
            retry_session,
            42,
            request.request_id,
            True,
            now=now,
        )
        self.assertTrue(retry_result["applied"])
        retry_session.commit.assert_not_awaited()

    async def test_expired_confirmation_does_not_apply_change(self) -> None:
        now = datetime(2026, 1, 1, 0, 11, tzinfo=timezone.utc)
        request = self.make_request(expires_at=now - timedelta(seconds=1))
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[request, self.make_account(), request])
        session.add = Mock()
        session.commit = AsyncMock()

        result = await confirm_daily_limit_change(
            session,
            42,
            request.request_id,
            True,
            now=now,
        )

        self.assertEqual(result["status"], "EXPIRED")
        self.assertFalse(result["applied"])
        self.assertEqual(request.status, "EXPIRED")
        session.commit.assert_awaited_once()
        self.assertEqual(session.add.call_args.args[0].event_type, "EXPIRED")

    async def test_confirmation_marks_changed_active_limit_as_conflicted(self) -> None:
        request = self.make_request()
        changed_limit = self.make_limit()
        changed_limit.daily_limit = Decimal("15000.00")
        now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
        session = AsyncMock()
        session.scalar = AsyncMock(
            side_effect=[request, self.make_account(), request, changed_limit]
        )
        session.add = Mock()
        session.commit = AsyncMock()

        result = await confirm_daily_limit_change(
            session,
            42,
            request.request_id,
            True,
            now=now,
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertFalse(result["applied"])
        self.assertEqual(changed_limit.daily_limit, Decimal("15000.00"))
        session.commit.assert_awaited_once()
        self.assertEqual(session.add.call_args.args[0].event_type, "CONFLICTED")

    async def test_confirmation_requires_explicit_true(self) -> None:
        with self.assertRaises(DailyLimitPolicyError):
            await confirm_daily_limit_change(AsyncMock(), 42, uuid4(), False)

    async def test_wrong_customer_cannot_confirm_another_customers_request(self) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        with self.assertRaises(DailyLimitNotFound):
            await confirm_daily_limit_change(session, 99, uuid4(), True)

        session.scalar.assert_awaited_once()
        query = str(session.scalar.call_args.args[0].compile())
        self.assertIn("daily_limit_change_requests.customer_id", query)

    async def test_cancel_records_audit_event_without_applying_change(self) -> None:
        request = self.make_request()
        now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
        session = AsyncMock()
        session.scalar = AsyncMock(
            side_effect=[request, self.make_account(), request]
        )
        session.add = Mock()
        session.commit = AsyncMock()

        result = await cancel_daily_limit_change(
            session,
            42,
            request.request_id,
            now=now,
        )

        self.assertEqual(result["status"], "CANCELLED")
        self.assertFalse(result["applied"])
        self.assertEqual(request.status, "CANCELLED")
        session.commit.assert_awaited_once()
        self.assertEqual(session.add.call_args.args[0].event_type, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
