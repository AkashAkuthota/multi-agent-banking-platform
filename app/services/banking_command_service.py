"""Durable proposal, confirmation, and cancellation for sandbox write commands."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    DailyLimitChangeRequest,
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
from app.services.daily_limit_service import (
    DailyLimitConflict,
    DailyLimitNotFound,
    cancel_daily_limit_change,
    confirm_daily_limit_change,
)

COMMAND_TTL = timedelta(minutes=10)


def _transfer_result(
    request: TransferRequest,
    *,
    applied: bool | None = None,
) -> dict[str, str | bool | None]:
    if applied is None:
        applied = request.status == "CONFIRMED"
    return {
        "request_id": str(request.request_id),
        "source_account_id": request.source_account_ref,
        "destination_account_id": request.destination_account_ref,
        "amount": str(request.amount),
        "currency": request.currency,
        "status": request.status,
        "expires_at": request.expires_at.isoformat(),
        "applied": applied,
        "source_transaction_id": request.source_transaction_id,
        "destination_transaction_id": request.destination_transaction_id,
    }


def _service_request_result(
    proposal: ServiceRequestProposal,
    *,
    applied: bool | None = None,
) -> dict[str, str | bool | None]:
    if applied is None:
        applied = proposal.status == "CONFIRMED"
    return {
        "request_id": str(proposal.request_id),
        "request_type": proposal.request_type,
        "status": proposal.status,
        "expires_at": proposal.expires_at.isoformat(),
        "applied": applied,
        "service_request_id": proposal.created_service_request_id,
    }


def _transfer_audit(
    request: TransferRequest,
    event_type: str,
    occurred_at: datetime,
) -> TransferAuditEvent:
    return TransferAuditEvent(
        request_id=request.request_id,
        customer_id=request.customer_id,
        event_type=event_type,
        source_account_ref=request.source_account_ref,
        destination_account_ref=request.destination_account_ref,
        amount=request.amount,
        currency=request.currency,
        occurred_at=occurred_at,
    )


def _service_request_audit(
    proposal: ServiceRequestProposal,
    event_type: str,
    occurred_at: datetime,
) -> ServiceRequestProposalAuditEvent:
    return ServiceRequestProposalAuditEvent(
        request_id=proposal.request_id,
        customer_id=proposal.customer_id,
        event_type=event_type,
        request_type=proposal.request_type,
        occurred_at=occurred_at,
    )


async def _owned_transfer_accounts(
    session: AsyncSession,
    customer_id: int,
    source_account_id: str,
    destination_account_id: str,
    *,
    lock: bool,
) -> tuple[Account, Account]:
    if source_account_id == destination_account_id:
        raise BankingCommandPolicyError(
            "Source and destination accounts must be different"
        )
    statement = (
        select(Account)
        .where(
            Account.customer_id == customer_id,
            Account.account_id.in_(
                {source_account_id, destination_account_id}
            ),
        )
        .order_by(Account.id)
    )
    if lock:
        statement = statement.with_for_update()
    accounts = list((await session.scalars(statement)).all())
    if len(accounts) != 2:
        raise DailyLimitNotFound("One or both accounts were not found")
    accounts_by_ref = {account.account_id: account for account in accounts}
    return (
        accounts_by_ref[source_account_id],
        accounts_by_ref[destination_account_id],
    )


async def propose_internal_transfer(
    session: AsyncSession,
    customer_id: int,
    source_account_id: str,
    destination_account_id: str,
    amount: Decimal,
    idempotency_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool | None]:
    amount = validate_transfer_amount(amount)
    if not idempotency_key or len(idempotency_key) > 128:
        raise BankingCommandPolicyError(
            "Idempotency key must contain 1 to 128 characters"
        )
    now = now or datetime.now(timezone.utc)
    source, destination = await _owned_transfer_accounts(
        session,
        customer_id,
        source_account_id,
        destination_account_id,
        lock=True,
    )
    existing = await session.scalar(
        select(TransferRequest).where(
            TransferRequest.customer_id == customer_id,
            TransferRequest.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.source_account_id != source.id
            or existing.destination_account_id != destination.id
            or existing.amount != amount
        ):
            raise DailyLimitConflict(
                "Idempotency key was already used for a different transfer"
            )
        return _transfer_result(existing)
    if source.status != "ACTIVE" or destination.status != "ACTIVE":
        raise BankingCommandPolicyError("Both accounts must be active")
    if source.currency != destination.currency:
        raise BankingCommandPolicyError(
            "Transfers require both accounts to use the same currency"
        )
    if source.balance < amount:
        raise BankingCommandPolicyError("Insufficient available balance")
    active_request = await session.scalar(
        select(TransferRequest).where(
            TransferRequest.customer_id == customer_id,
            TransferRequest.status == "PENDING",
            TransferRequest.source_account_id == source.id,
        )
    )
    if active_request is not None and active_request.expires_at > now:
        raise DailyLimitConflict(
            "A transfer from this account is already awaiting confirmation"
        )
    if active_request is not None:
        active_request.status = "EXPIRED"
        session.add(_transfer_audit(active_request, "EXPIRED", now))
        await session.flush()

    request = TransferRequest(
        request_id=uuid4(),
        idempotency_key=idempotency_key,
        customer_id=customer_id,
        source_account_ref=source.account_id,
        source_account_id=source.id,
        destination_account_ref=destination.account_id,
        destination_account_id=destination.id,
        amount=amount,
        currency=source.currency,
        status="PENDING",
        created_at=now,
        expires_at=now + COMMAND_TTL,
    )
    try:
        session.add(request)
        await session.flush()
        session.add(_transfer_audit(request, "PROPOSED", now))
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        duplicate = await session.scalar(
            select(TransferRequest).where(
                TransferRequest.customer_id == customer_id,
                TransferRequest.idempotency_key == idempotency_key,
            )
        )
        if (
            duplicate is not None
            and duplicate.source_account_id == source.id
            and duplicate.destination_account_id == destination.id
            and duplicate.amount == amount
        ):
            return _transfer_result(duplicate)
        if duplicate is not None:
            raise DailyLimitConflict(
                "Idempotency key was already used for a different transfer"
            ) from error
        active_request = await session.scalar(
            select(TransferRequest).where(
                TransferRequest.customer_id == customer_id,
                TransferRequest.status == "PENDING",
                TransferRequest.source_account_id == source.id,
            )
        )
        if active_request is not None:
            raise DailyLimitConflict(
                "A transfer from this account is already awaiting confirmation"
            ) from error
        raise
    return _transfer_result(request)


async def confirm_internal_transfer(
    session: AsyncSession,
    customer_id: int,
    request_id: UUID,
    confirmed: bool,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool | None]:
    if confirmed is not True:
        raise BankingCommandPolicyError("Explicit confirmation is required")
    now = now or datetime.now(timezone.utc)
    initial = await session.scalar(
        select(TransferRequest).where(
            TransferRequest.request_id == request_id,
            TransferRequest.customer_id == customer_id,
        )
    )
    if initial is None:
        raise DailyLimitNotFound("Transfer request not found")
    source, destination = await _owned_transfer_accounts(
        session,
        customer_id,
        initial.source_account_ref,
        initial.destination_account_ref,
        lock=True,
    )
    request = await session.scalar(
        select(TransferRequest)
        .where(
            TransferRequest.request_id == request_id,
            TransferRequest.customer_id == customer_id,
        )
        .with_for_update()
    )
    if request is None:
        raise DailyLimitNotFound("Transfer request not found")
    if request.status == "CONFIRMED":
        return _transfer_result(request, applied=True)
    if request.status != "PENDING":
        raise DailyLimitConflict(
            f"Transfer cannot be confirmed from {request.status} state"
        )
    if request.expires_at <= now:
        request.status = "EXPIRED"
        session.add(_transfer_audit(request, "EXPIRED", now))
        await session.commit()
        return _transfer_result(request)
    if (
        source.id != request.source_account_id
        or destination.id != request.destination_account_id
        or source.status != "ACTIVE"
        or destination.status != "ACTIVE"
        or source.currency != request.currency
        or destination.currency != request.currency
        or source.balance < request.amount
    ):
        request.status = "CONFLICTED"
        session.add(_transfer_audit(request, "CONFLICTED", now))
        await session.commit()
        return _transfer_result(request)

    source.balance -= request.amount
    destination.balance += request.amount
    source_transaction_id = f"TRF_OUT_{request.request_id.hex[:16]}"
    destination_transaction_id = f"TRF_IN_{request.request_id.hex[:16]}"
    session.add_all(
        [
            Transaction(
                transaction_id=source_transaction_id,
                account_id=source.id,
                transaction_type="TRANSFER_OUT",
                amount=request.amount,
                merchant="Internal transfer",
                category="Transfer",
                transaction_date=now,
                status="COMPLETED",
                description=f"Transfer to {destination.account_id}",
            ),
            Transaction(
                transaction_id=destination_transaction_id,
                account_id=destination.id,
                transaction_type="TRANSFER_IN",
                amount=request.amount,
                merchant="Internal transfer",
                category="Transfer",
                transaction_date=now,
                status="COMPLETED",
                description=f"Transfer from {source.account_id}",
            ),
        ]
    )
    request.source_transaction_id = source_transaction_id
    request.destination_transaction_id = destination_transaction_id
    request.status = "CONFIRMED"
    request.confirmed_at = now
    session.add(_transfer_audit(request, "CONFIRMED", now))
    await session.commit()
    return _transfer_result(request, applied=True)


async def cancel_internal_transfer(
    session: AsyncSession,
    customer_id: int,
    request_id: UUID,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool | None]:
    now = now or datetime.now(timezone.utc)
    request = await session.scalar(
        select(TransferRequest)
        .where(
            TransferRequest.request_id == request_id,
            TransferRequest.customer_id == customer_id,
        )
        .with_for_update()
    )
    if request is None:
        raise DailyLimitNotFound("Transfer request not found")
    if request.status == "CANCELLED":
        return _transfer_result(request)
    if request.status != "PENDING":
        raise DailyLimitConflict(
            f"Transfer cannot be cancelled from {request.status} state"
        )
    event_type = "EXPIRED" if request.expires_at <= now else "CANCELLED"
    request.status = event_type
    session.add(_transfer_audit(request, event_type, now))
    await session.commit()
    return _transfer_result(request)


async def propose_service_request(
    session: AsyncSession,
    customer_id: int,
    request_type: str,
    idempotency_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool | None]:
    request_type = validate_service_request_type(request_type)
    if not idempotency_key or len(idempotency_key) > 128:
        raise BankingCommandPolicyError(
            "Idempotency key must contain 1 to 128 characters"
        )
    now = now or datetime.now(timezone.utc)
    existing = await session.scalar(
        select(ServiceRequestProposal).where(
            ServiceRequestProposal.customer_id == customer_id,
            ServiceRequestProposal.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_type != request_type:
            raise DailyLimitConflict(
                "Idempotency key was already used for another service request"
            )
        return _service_request_result(existing)

    proposal = ServiceRequestProposal(
        request_id=uuid4(),
        idempotency_key=idempotency_key,
        customer_id=customer_id,
        request_type=request_type,
        status="PENDING",
        created_at=now,
        expires_at=now + COMMAND_TTL,
    )
    try:
        session.add(proposal)
        await session.flush()
        session.add(_service_request_audit(proposal, "PROPOSED", now))
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        duplicate = await session.scalar(
            select(ServiceRequestProposal).where(
                ServiceRequestProposal.customer_id == customer_id,
                ServiceRequestProposal.idempotency_key == idempotency_key,
            )
        )
        if duplicate is not None and duplicate.request_type == request_type:
            return _service_request_result(duplicate)
        if duplicate is not None:
            raise DailyLimitConflict(
                "Idempotency key was already used for another service request"
            ) from error
        raise error
    return _service_request_result(proposal)


async def confirm_service_request(
    session: AsyncSession,
    customer_id: int,
    request_id: UUID,
    confirmed: bool,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool | None]:
    if confirmed is not True:
        raise BankingCommandPolicyError("Explicit confirmation is required")
    now = now or datetime.now(timezone.utc)
    proposal = await session.scalar(
        select(ServiceRequestProposal)
        .where(
            ServiceRequestProposal.request_id == request_id,
            ServiceRequestProposal.customer_id == customer_id,
        )
        .with_for_update()
    )
    if proposal is None:
        raise DailyLimitNotFound("Service request proposal not found")
    if proposal.status == "CONFIRMED":
        return _service_request_result(proposal, applied=True)
    if proposal.status != "PENDING":
        raise DailyLimitConflict(
            f"Service request cannot be confirmed from {proposal.status} state"
        )
    if proposal.expires_at <= now:
        proposal.status = "EXPIRED"
        session.add(_service_request_audit(proposal, "EXPIRED", now))
        await session.commit()
        return _service_request_result(proposal)

    service_request_id = f"SR_{proposal.request_id.hex[:20]}"
    session.add(
        ServiceRequest(
            request_id=service_request_id,
            customer_id=customer_id,
            request_type=proposal.request_type,
            status="PENDING",
            created_at=now,
        )
    )
    await session.flush()
    proposal.status = "CONFIRMED"
    proposal.confirmed_at = now
    proposal.created_service_request_id = service_request_id
    session.add(_service_request_audit(proposal, "CONFIRMED", now))
    await session.commit()
    return _service_request_result(proposal, applied=True)


async def cancel_service_request_proposal(
    session: AsyncSession,
    customer_id: int,
    request_id: UUID,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool | None]:
    now = now or datetime.now(timezone.utc)
    proposal = await session.scalar(
        select(ServiceRequestProposal)
        .where(
            ServiceRequestProposal.request_id == request_id,
            ServiceRequestProposal.customer_id == customer_id,
        )
        .with_for_update()
    )
    if proposal is None:
        raise DailyLimitNotFound("Service request proposal not found")
    if proposal.status == "CANCELLED":
        return _service_request_result(proposal)
    if proposal.status != "PENDING":
        raise DailyLimitConflict(
            f"Service request cannot be cancelled from {proposal.status} state"
        )
    event_type = "EXPIRED" if proposal.expires_at <= now else "CANCELLED"
    proposal.status = event_type
    session.add(_service_request_audit(proposal, event_type, now))
    await session.commit()
    return _service_request_result(proposal)


async def decide_customer_proposal(
    session: AsyncSession,
    customer_id: int,
    request_id: UUID,
    decision: str,
) -> dict[str, str | bool | None]:
    """Confirm or cancel exactly one proposal owned by the authenticated customer."""
    if decision not in {"confirm", "cancel"}:
        raise BankingCommandPolicyError("Unsupported proposal decision")

    proposal_models = (
        DailyLimitChangeRequest,
        TransferRequest,
        ServiceRequestProposal,
    )
    matching_models = []
    for proposal_model in proposal_models:
        proposal = await session.scalar(
            select(proposal_model).where(
                proposal_model.request_id == request_id,
                proposal_model.customer_id == customer_id,
            )
        )
        if proposal is not None:
            matching_models.append(proposal_model)

    if not matching_models:
        raise DailyLimitNotFound("Pending proposal not found")
    if len(matching_models) != 1:
        raise DailyLimitConflict("Proposal ID is ambiguous")

    if matching_models[0] is DailyLimitChangeRequest:
        if decision == "confirm":
            return await confirm_daily_limit_change(
                session,
                customer_id,
                request_id,
                True,
            )
        return await cancel_daily_limit_change(session, customer_id, request_id)

    if matching_models[0] is TransferRequest:
        if decision == "confirm":
            return await confirm_internal_transfer(
                session,
                customer_id,
                request_id,
                True,
            )
        return await cancel_internal_transfer(session, customer_id, request_id)

    if decision == "confirm":
        return await confirm_service_request(session, customer_id, request_id, True)
    return await cancel_service_request_proposal(session, customer_id, request_id)
