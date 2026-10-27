"""Authenticated-customer dependency."""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.jwt import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_customer(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> int:
    """Return the customer ID from a valid bearer access token."""
    if credentials is None:
        raise _unauthorized()

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise _unauthorized() from None

    subject = payload.get("sub") if isinstance(payload, dict) else None
    if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal():
        raise _unauthorized()

    try:
        customer_id = int(subject)
    except ValueError:
        raise _unauthorized() from None
    if customer_id <= 0:
        raise _unauthorized()
    return customer_id


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
