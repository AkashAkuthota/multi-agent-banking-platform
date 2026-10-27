"""Authentication endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_customer
from app.core.config import settings
from app.core.database import get_db
from app.core.jwt import create_access_token
from app.core.login_rate_limit import LoginAttemptLimiter
from app.core.security import verify_password
from app.models import Customer
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])
login_attempt_limiter = LoginAttemptLimiter(
    max_attempts=settings.LOGIN_RATE_LIMIT_ATTEMPTS,
    window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)


@router.get("/me")
async def get_me(
    customer_id: Annotated[int, Depends(get_current_customer)],
) -> dict[str, int]:
    return {"customer_id": customer_id}


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    normalized_email = request.email.strip().casefold()
    retry_after = await login_attempt_limiter.reserve(normalized_email)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    customer = await session.scalar(
        select(Customer)
        .options(selectinload(Customer.credential))
        .where(Customer.email == request.email)
    )
    credential = customer.credential if customer is not None else None
    if (
        credential is None
        or not credential.is_active
        or not verify_password(request.password, credential.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    await login_attempt_limiter.clear(normalized_email)

    return LoginResponse(
        access_token=create_access_token(customer.id),
        token_type="bearer",
    )
