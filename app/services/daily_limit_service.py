"""Customer-scoped sandbox daily-limit workflow and deterministic policy."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    DailyAccountLimit,
    DailyLimitAuditEvent,
    DailyLimitChangeRequest,
)
from app.policies.daily_limit import (
    DailyLimitPolicyError,
    validate_active_account,
    validate_daily_limit,
)

REQUEST_TTL = timedelta(minutes=10)


class DailyLimitError(Exception):
    """Base error for expected daily-limit workflow failures."""


class DailyLimitNotFound(DailyLimitError):
    """The account or request is not visible to the authenticated customer."""


class DailyLimitConflict(DailyLimitError):
    """A request conflicts with the current workflow or idempotency state."""


class DailyLimitConfigurationError(DailyLimitError):
    """The account has no valid configured daily-limit record."""


def _request_result(
    request: DailyLimitChangeRequest,
    *,
    applied: bool | None = None,
) -> dict[str, str | bool]:
    if applied is None:
        applied = request.status == "CONFIRMED"
    return {
        "request_id": str(request.request_id),
        "account_id": request.account_ref,
        "previous_limit": str(request.previous_limit),
        "requested_limit": str(request.requested_limit),
        "currency": request.currency,
        "status": request.status,
        "expires_at": request.expires_at.isoformat(),
        "applied": applied,
    }


def _audit(
    request: DailyLimitChangeRequest,
    event_type: str,
    *,
    occurred_at: datetime | None = None,
) -> DailyLimitAuditEvent:
    return DailyLimitAuditEvent(
        request_id=request.request_id,
        customer_id=request.customer_id,
        event_type=event_type,
        previous_limit=request.previous_limit,
        requested_limit=request.requested_limit,
        currency=request.currency,
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )


async def _get_owned_account(
    session: AsyncSession,
    customer_id: int,
    account_id: str,
) -> Account:
    account = await session.scalar(
        select(Account)
        .where(
            Account.account_id == account_id,
            Account.customer_id == customer_id,
        )
        .with_for_update()
    )
    if account is None:
        raise DailyLimitNotFound("Account not found")
    return account


async def _get_account_limit(
    session: AsyncSession,
    account: Account,
) -> DailyAccountLimit:
    limit_record = await session.scalar(
        select(DailyAccountLimit)
        .where(DailyAccountLimit.account_id == account.id)
        .with_for_update()
    )
    if limit_record is None or limit_record.currency != account.currency:
        raise DailyLimitConfigurationError(
            "The account daily limit is not configured for its currency"
        )
    return limit_record


async def get_customer_daily_limit(
    session: AsyncSession,
    customer_id: int,
    account_id: str,
) -> dict[str, str]:
    account = await session.scalar(
        select(Account).where(
            Account.account_id == account_id,
            Account.customer_id == customer_id,
        )
    )
    if account is None:
        raise DailyLimitNotFound("Account not found")
    limit_record = await _get_account_limit(session, account)
    return {
        "account_id": account.account_id,
        "daily_limit": str(limit_record.daily_limit),
        "currency": limit_record.currency,
        "updated_at": limit_record.updated_at.isoformat(),
    }


async def propose_daily_limit_change(
    session: AsyncSession,
    customer_id: int,
    account_id: str,
    requested_limit: Decimal,
    idempotency_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool]:
    requested_limit = validate_daily_limit(requested_limit)
    if not idempotency_key or len(idempotency_key) > 128:
        raise DailyLimitPolicyError("Idempotency key must contain 1 to 128 characters")
    now = now or datetime.now(timezone.utc)

    account = await _get_owned_account(session, customer_id, account_id)
    existing = await session.scalar(
        select(DailyLimitChangeRequest).where(
            DailyLimitChangeRequest.customer_id == customer_id,
            DailyLimitChangeRequest.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.account_id != account.id
            or existing.account_ref != account_id
            or existing.requested_limit != requested_limit
        ):
            raise DailyLimitConflict(
                "Idempotency key was already used for a different request"
            )
        return _request_result(existing)

    validate_active_account(account.status)
    limit_record = await _get_account_limit(session, account)
    if requested_limit == limit_record.daily_limit:
        raise DailyLimitPolicyError("The requested daily limit is already active")

    pending = await session.scalar(
        select(DailyLimitChangeRequest)
        .where(
            DailyLimitChangeRequest.account_id == account.id,
            DailyLimitChangeRequest.status == "PENDING",
        )
        .with_for_update()
    )
    if pending is not None:
        if pending.expires_at <= now:
            pending.status = "EXPIRED"
            session.add(_audit(pending, "EXPIRED", occurred_at=now))
            await session.flush()
        else:
            raise DailyLimitConflict(
                "A daily-limit change is already awaiting confirmation"
            )

    request = DailyLimitChangeRequest(
        request_id=uuid4(),
        idempotency_key=idempotency_key,
        customer_id=customer_id,
        account_ref=account_id,
        account_id=account.id,
        previous_limit=limit_record.daily_limit,
        requested_limit=requested_limit,
        currency=limit_record.currency,
        status="PENDING",
        created_at=now,
        expires_at=now + REQUEST_TTL,
    )
    try:
        session.add(request)
        await session.flush()
        session.add(_audit(request, "PROPOSED", occurred_at=now))
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        duplicate = await session.scalar(
            select(DailyLimitChangeRequest).where(
                DailyLimitChangeRequest.customer_id == customer_id,
                DailyLimitChangeRequest.idempotency_key == idempotency_key,
            )
        )
        if (
            duplicate is not None
            and duplicate.account_id == account.id
            and duplicate.account_ref == account_id
            and duplicate.requested_limit == requested_limit
        ):
            return _request_result(duplicate)
        if duplicate is not None:
            raise DailyLimitConflict(
                "Idempotency key was already used for a different request"
            ) from error
        active_request = await session.scalar(
            select(DailyLimitChangeRequest).where(
                DailyLimitChangeRequest.account_id == account.id,
                DailyLimitChangeRequest.status == "PENDING",
            )
        )
        if active_request is not None:
            raise DailyLimitConflict(
                "A daily-limit change is already awaiting confirmation"
            ) from error
        raise
    return _request_result(request)


async def confirm_daily_limit_change(
    session: AsyncSession,
    customer_id: int,
    request_id: UUID,
    confirmed: bool,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool]:
    if confirmed is not True:
        raise DailyLimitPolicyError("Explicit confirmation is required")
    now = now or datetime.now(timezone.utc)
    initial_request = await session.scalar(
        select(DailyLimitChangeRequest).where(
            DailyLimitChangeRequest.request_id == request_id,
            DailyLimitChangeRequest.customer_id == customer_id,
        )
    )
    if initial_request is None:
        raise DailyLimitNotFound("Change request not found")
    account = await session.scalar(
        select(Account)
        .where(
            Account.id == initial_request.account_id,
            Account.customer_id == customer_id,
        )
        .with_for_update()
    )
    if account is None:
        raise DailyLimitNotFound("Change request not found")
    request = await session.scalar(
        select(DailyLimitChangeRequest)
        .where(
            DailyLimitChangeRequest.request_id == request_id,
            DailyLimitChangeRequest.customer_id == customer_id,
        )
        .with_for_update()
    )
    if request is None:
        raise DailyLimitNotFound("Change request not found")
    if request.status == "CONFIRMED":
        return _request_result(request, applied=True)
    if request.status != "PENDING":
        raise DailyLimitConflict(
            f"Change request cannot be confirmed from {request.status} state"
        )
    if request.expires_at <= now:
        request.status = "EXPIRED"
        session.add(_audit(request, "EXPIRED", occurred_at=now))
        await session.commit()
        return _request_result(request)

    try:
        validate_active_account(account.status)
    except DailyLimitPolicyError:
        request.status = "CONFLICTED"
        session.add(_audit(request, "CONFLICTED", occurred_at=now))
        await session.commit()
        return _request_result(request)

    limit_record = await _get_account_limit(session, account)
    if limit_record.daily_limit != request.previous_limit:
        request.status = "CONFLICTED"
        session.add(_audit(request, "CONFLICTED", occurred_at=now))
        await session.commit()
        return _request_result(request)

    validate_daily_limit(request.requested_limit)
    limit_record.daily_limit = request.requested_limit
    limit_record.updated_at = now
    request.status = "CONFIRMED"
    request.confirmed_at = now
    session.add(_audit(request, "CONFIRMED", occurred_at=now))
    await session.commit()
    return _request_result(request, applied=True)


async def cancel_daily_limit_change(
    session: AsyncSession,
    customer_id: int,
    request_id: UUID,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool]:
    now = now or datetime.now(timezone.utc)
    initial_request = await session.scalar(
        select(DailyLimitChangeRequest).where(
            DailyLimitChangeRequest.request_id == request_id,
            DailyLimitChangeRequest.customer_id == customer_id,
        )
    )
    if initial_request is None:
        raise DailyLimitNotFound("Change request not found")
    account = await session.scalar(
        select(Account)
        .where(
            Account.id == initial_request.account_id,
            Account.customer_id == customer_id,
        )
        .with_for_update()
    )
    if account is None:
        raise DailyLimitNotFound("Change request not found")
    request = await session.scalar(
        select(DailyLimitChangeRequest)
        .where(
            DailyLimitChangeRequest.request_id == request_id,
            DailyLimitChangeRequest.customer_id == customer_id,
        )
        .with_for_update()
    )
    if request is None:
        raise DailyLimitNotFound("Change request not found")
    if request.status == "CANCELLED":
        return _request_result(request)
    if request.status != "PENDING":
        raise DailyLimitConflict(
            f"Change request cannot be cancelled from {request.status} state"
        )
    event_type = "EXPIRED" if request.expires_at <= now else "CANCELLED"
    request.status = event_type
    session.add(_audit(request, event_type, occurred_at=now))
    await session.commit()
    return _request_result(request)
