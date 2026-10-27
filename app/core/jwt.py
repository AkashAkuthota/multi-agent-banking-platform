"""JWT access-token utilities."""

from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


def create_access_token(customer_id: int) -> str:
    """Create a signed access token for a customer."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(customer_id),
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()
        ),
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """Decode and validate a signed access token."""
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
