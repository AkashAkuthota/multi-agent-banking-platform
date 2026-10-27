"""Tests for transfer and service-request command workflows."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from app.models import (
    Account,
    ServiceRequest,
    ServiceRequestProposal,
    ServiceRequestProposalAuditEvent,
    Transaction,
    TransferAuditEvent,
    TransferRequest,
)
from app.policies.banking_commands import (
    BankingCommandPolicyError,
    validate_service_request_type,
    validate_transfer_amount,
)
from app.services.banking_command_service import (
    cancel_internal_transfer,
    cancel_service_request_proposal,
    confirm_internal_transfer,
    confirm_service_request,
    decide_customer_proposal,
    propose_internal_transfer,
    propose_service_request,
)
from app.services.daily_limit_service import DailyLimitNotFound


class BankingCommandPolicyTests(unittest.TestCase):
    def test_transfer_amount_policy(self) -> None:
        self.assertEqual(
            validate_transfer_amount(Decimal("100.50")),
            Decimal("100.50"),
        )
        for amount in (
            Decimal("0"),
            Decimal("-1"),
            Decimal("50000.01"),
            Decimal("12.345"),
            Decimal("NaN"),
        ):
            with self.subTest(amount=amount):
                with self.assertRaises(BankingCommandPolicyError):
                    validate_transfer_amount(amount)

    def test_service_request_type_policy_is_allow_listed(self) -> None:
        self.assertEqual(validate_service_request_type(" cheque_book "), "CHEQUE_BOOK")
        with self.assertRaises(BankingCommandPolicyError):
            validate_service_request_type("ACCOUNT_CLOSURE")


class BankingCommandWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_account(
        account_id: str,
        pk: int,
        balance: str,
        *,
        customer_id: int = 42,
        currency: str = "INR",
        status: str = "ACTIVE",
    ) -> Account:
        return Account(
            id=pk,
            account_id=account_id,
            customer_id=customer_id,
            account_type="SAVINGS",
            account_number=f"XXXXXX{pk:04d}",
            balance=Decimal(balance),
            currency=currency,
            status=status,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    @staticmethod
    def make_transfer(
        *,
        status: str = "PENDING",
        expires_at: datetime | None = None,
    ) -> TransferRequest:
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return TransferRequest(
            request_id=uuid4(),
            idempotency_key="transfer-1",
            customer_id=42,
            source_account_ref="ACC_001",
            source_account_id=1,
            destination_account_ref="ACC_002",
            destination_account_id=2,
            amount=Decimal("100.00"),
            currency="INR",
            status=status,
            created_at=created_at,
            expires_at=expires_at or created_at + timedelta(minutes=10),
        )

    @staticmethod
    def set_locked_accounts(
        session: AsyncMock,
        source: Account,
        destination: Account,
    ) -> None:
        session.scalars = AsyncMock(
            return_value=Mock(all=Mock(return_value=[source, destination]))
        )

    async def test_transfer_proposal_is_pending_and_idempotent(self) -> None:
        session = AsyncMock()
        self.set_locked_accounts(
            session,
            self.make_account("ACC_001", 1, "1000.00"),
            self.make_account("ACC_002", 2, "500.00"),
        )
        session.scalar = AsyncMock(side_effect=[None, None])
        session.add = Mock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = await propose_internal_transfer(
            session,
            42,
            "ACC_001",
            "ACC_002",
            Decimal("100.00"),
            "transfer-1",
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(result["status"], "PENDING")
        self.assertFalse(result["applied"])
        session.commit.assert_awaited_once()
        request, audit = [call.args[0] for call in session.add.call_args_list]
        self.assertIsInstance(request, TransferRequest)
        self.assertIsInstance(audit, TransferAuditEvent)
        self.assertEqual(audit.event_type, "PROPOSED")

    async def test_confirmed_transfer_retry_reports_that_funds_were_applied(self) -> None:
        existing = self.make_transfer(status="CONFIRMED")
        session = AsyncMock()
        self.set_locked_accounts(
            session,
            self.make_account("ACC_001", 1, "900.00"),
            self.make_account("ACC_002", 2, "600.00"),
        )
        session.scalar = AsyncMock(return_value=existing)

        result = await propose_internal_transfer(
            session,
            42,
            "ACC_001",
            "ACC_002",
            Decimal("100.00"),
            existing.idempotency_key,
        )

        self.assertEqual(result["status"], "CONFIRMED")
        self.assertTrue(result["applied"])
        session.commit.assert_not_awaited()

    async def test_transfer_ownership_query_requires_both_customer_accounts(self) -> None:
        session = AsyncMock()
        session.scalars = AsyncMock(
            return_value=Mock(all=Mock(return_value=[self.make_account("ACC_001", 1, "1000.00")]))
        )

        with self.assertRaises(DailyLimitNotFound):
            await propose_internal_transfer(
                session,
                42,
                "ACC_001",
                "ACC_OTHER",
                Decimal("100.00"),
                "transfer-1",
            )

        query = str(session.scalars.call_args.args[0].compile())
        self.assertIn("accounts.customer_id", query)
        self.assertIn("accounts.account_id", query)

    async def test_transfer_rejects_currency_mismatch_and_same_account(self) -> None:
        session = AsyncMock()
        self.set_locked_accounts(
            session,
            self.make_account("ACC_001", 1, "1000.00"),
            self.make_account("ACC_002", 2, "500.00", currency="USD"),
        )
        session.scalar = AsyncMock(return_value=None)
        with self.assertRaises(BankingCommandPolicyError):
            await propose_internal_transfer(
                session,
                42,
                "ACC_001",
                "ACC_002",
                Decimal("100.00"),
                "transfer-1",
            )
        session.scalar.assert_awaited_once()

        with self.assertRaises(BankingCommandPolicyError):
            await propose_internal_transfer(
                session,
                42,
                "ACC_001",
                "ACC_001",
                Decimal("100.00"),
                "transfer-2",
            )

    async def test_confirmation_moves_funds_and_writes_two_transactions(self) -> None:
        request = self.make_transfer()
        source = self.make_account("ACC_001", 1, "1000.00")
        destination = self.make_account("ACC_002", 2, "500.00")
        now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[request, request])
        self.set_locked_accounts(session, source, destination)
        session.add = Mock()
        session.add_all = Mock()
        session.commit = AsyncMock()

        result = await confirm_internal_transfer(
            session,
            42,
            request.request_id,
            True,
            now=now,
        )

        self.assertTrue(result["applied"])
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertEqual(source.balance, Decimal("900.00"))
        self.assertEqual(destination.balance, Decimal("600.00"))
        self.assertTrue(str(result["source_transaction_id"]).startswith("TRF_OUT_"))
        transactions = session.add_all.call_args.args[0]
        self.assertEqual(len(transactions), 2)
        self.assertTrue(all(isinstance(item, Transaction) for item in transactions))
        self.assertEqual(request.confirmed_at, now)
        session.commit.assert_awaited_once()
        self.assertEqual(session.add.call_args.args[0].event_type, "CONFIRMED")

    async def test_confirmed_transfer_retry_does_not_apply_twice(self) -> None:
        request = self.make_transfer(status="CONFIRMED")
        request.source_transaction_id = "TRF_OUT_existing"
        request.destination_transaction_id = "TRF_IN_existing"
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[request, request])
        self.set_locked_accounts(
            session,
            self.make_account("ACC_001", 1, "900.00"),
            self.make_account("ACC_002", 2, "600.00"),
        )

        result = await confirm_internal_transfer(
            session,
            42,
            request.request_id,
            True,
        )

        self.assertTrue(result["applied"])
        self.assertEqual(result["source_transaction_id"], "TRF_OUT_existing")
        session.add_all.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_insufficient_balance_at_confirmation_marks_conflicted(self) -> None:
        request = self.make_transfer()
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[request, request])
        self.set_locked_accounts(
            session,
            self.make_account("ACC_001", 1, "50.00"),
            self.make_account("ACC_002", 2, "500.00"),
        )
        session.add = Mock()
        session.commit = AsyncMock()

        result = await confirm_internal_transfer(
            session,
            42,
            request.request_id,
            True,
            now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertFalse(result["applied"])
        session.add_all.assert_not_called()

    async def test_transfer_requires_explicit_confirmation_and_cancellation_audits(self) -> None:
        request = self.make_transfer()
        with self.assertRaises(BankingCommandPolicyError):
            await confirm_internal_transfer(
                AsyncMock(),
                42,
                request.request_id,
                False,
            )

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=request)
        session.add = Mock()
        session.commit = AsyncMock()
        result = await cancel_internal_transfer(
            session,
            42,
            request.request_id,
            now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(session.add.call_args.args[0].event_type, "CANCELLED")

    async def test_service_request_creation_requires_confirmation(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        proposal = ServiceRequestProposal(
            request_id=uuid4(),
            idempotency_key="service-1",
            customer_id=42,
            request_type="STATEMENT",
            status="PENDING",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.add = Mock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = await propose_service_request(
            session,
            42,
            "statement",
            "service-1",
            now=now,
        )

        self.assertEqual(result["status"], "PENDING")
        self.assertFalse(result["applied"])
        added = [call.args[0] for call in session.add.call_args_list]
        self.assertTrue(any(isinstance(item, ServiceRequestProposal) for item in added))
        self.assertTrue(
            any(isinstance(item, ServiceRequestProposalAuditEvent) for item in added)
        )

        session.scalar = AsyncMock(return_value=proposal)
        session.add = Mock()
        session.commit = AsyncMock()
        confirmed = await confirm_service_request(
            session,
            42,
            proposal.request_id,
            True,
            now=now,
        )
        self.assertTrue(confirmed["applied"])
        self.assertEqual(proposal.status, "CONFIRMED")
        self.assertEqual(proposal.created_service_request_id[:3], "SR_")
        service_request = next(
            item
            for item in (call.args[0] for call in session.add.call_args_list)
            if isinstance(item, ServiceRequest)
        )
        self.assertEqual(service_request.status, "PENDING")

    async def test_service_request_proposal_is_allow_listed_and_idempotent(self) -> None:
        with self.assertRaises(BankingCommandPolicyError):
            await propose_service_request(
                AsyncMock(),
                42,
                "ACCOUNT_CLOSURE",
                "service-1",
            )

        existing = ServiceRequestProposal(
            request_id=uuid4(),
            idempotency_key="service-1",
            customer_id=42,
            request_type="STATEMENT",
            status="CONFIRMED",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
            created_service_request_id="SR_existing",
        )
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=existing)
        result = await propose_service_request(
            session,
            42,
            "STATEMENT",
            "service-1",
        )
        self.assertEqual(result["request_id"], str(existing.request_id))
        self.assertEqual(result["service_request_id"], "SR_existing")
        self.assertTrue(result["applied"])
        session.commit.assert_not_awaited()

    async def test_wrong_customer_cannot_confirm_transfer_or_service_request(self) -> None:
        for confirm, model in (
            (confirm_internal_transfer, TransferRequest),
            (confirm_service_request, ServiceRequestProposal),
        ):
            with self.subTest(model=model.__name__):
                session = AsyncMock()
                session.scalar = AsyncMock(return_value=None)
                with self.assertRaises(DailyLimitNotFound):
                    await confirm(session, 99, uuid4(), True)
                query = str(session.scalar.call_args.args[0].compile())
                self.assertIn(f"{model.__tablename__}.customer_id", query)

    async def test_service_request_cancellation_is_audited(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        proposal = ServiceRequestProposal(
            request_id=uuid4(),
            idempotency_key="service-1",
            customer_id=42,
            request_type="STATEMENT",
            status="PENDING",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=proposal)
        session.add = Mock()
        session.commit = AsyncMock()

        result = await cancel_service_request_proposal(
            session,
            42,
            proposal.request_id,
            now=now,
        )

        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(
            session.add.call_args.args[0].event_type,
            "CANCELLED",
        )
        session.commit.assert_awaited_once()

    async def test_customer_proposal_decision_dispatches_by_owned_record(self) -> None:
        request_id = uuid4()
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[object(), None, None])
        expected = {
            "request_id": str(request_id),
            "status": "CONFIRMED",
            "applied": True,
        }

        with patch(
            "app.services.banking_command_service.confirm_daily_limit_change",
            new=AsyncMock(return_value=expected),
        ) as confirm_limit:
            result = await decide_customer_proposal(
                session,
                42,
                request_id,
                "confirm",
            )

        self.assertEqual(result, expected)
        confirm_limit.assert_awaited_once_with(session, 42, request_id, True)
        self.assertEqual(session.scalar.await_count, 3)
        for query_call in session.scalar.await_args_list:
            query = str(query_call.args[0].compile())
            self.assertIn("customer_id", query)

    async def test_customer_decision_dispatches_transfer_and_service_request_records(
        self,
    ) -> None:
        request_id = uuid4()
        cases = (
            (
                [None, object(), None],
                "confirm_internal_transfer",
                "confirm",
            ),
            (
                [None, None, object()],
                "cancel_service_request_proposal",
                "cancel",
            ),
        )
        for query_results, service_name, decision in cases:
            with self.subTest(service_name=service_name):
                session = AsyncMock()
                session.scalar = AsyncMock(side_effect=query_results)
                expected = {
                    "request_id": str(request_id),
                    "status": "CONFIRMED" if decision == "confirm" else "CANCELLED",
                    "applied": decision == "confirm",
                }
                with patch(
                    f"app.services.banking_command_service.{service_name}",
                    new=AsyncMock(return_value=expected),
                ) as operation:
                    result = await decide_customer_proposal(
                        session,
                        42,
                        request_id,
                        decision,
                    )

                self.assertEqual(result, expected)
                if decision == "confirm":
                    operation.assert_awaited_once_with(
                        session,
                        42,
                        request_id,
                        True,
                    )
                else:
                    operation.assert_awaited_once_with(
                        session,
                        42,
                        request_id,
                    )

    async def test_customer_cannot_decide_a_proposal_owned_by_another_customer(self) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        with self.assertRaises(DailyLimitNotFound):
            await decide_customer_proposal(session, 99, uuid4(), "confirm")

        self.assertEqual(session.scalar.await_count, 3)
        for query_call in session.scalar.await_args_list:
            compiled_query = query_call.args[0].compile()
            self.assertIn("customer_id", str(compiled_query))
            self.assertIn(99, compiled_query.params.values())


if __name__ == "__main__":
    unittest.main()
