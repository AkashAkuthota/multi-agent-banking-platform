"""Tests for the authenticated-customer dependency."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.core.auth import bearer_scheme, get_current_customer
from app.core.config import settings
from app.core.jwt import create_access_token
from app.mcp.context import get_authenticated_execution_context


class CurrentCustomerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = (
            patch.object(settings, "JWT_SECRET_KEY", "test-secret-key-for-auth-tests-long-enough"),
            patch.object(settings, "JWT_ALGORITHM", "HS256"),
            patch.object(settings, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 5),
        )
        for setting_patch in self.settings:
            setting_patch.start()
            self.addCleanup(setting_patch.stop)

    @staticmethod
    def credentials(token: str) -> HTTPAuthorizationCredentials:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def test_valid_jwt_returns_customer_id(self) -> None:
        token = create_access_token(123)

        self.assertEqual(get_current_customer(self.credentials(token)), 123)

    def test_execution_context_uses_verified_customer_id(self) -> None:
        token = create_access_token(123)
        customer_id = get_current_customer(self.credentials(token))

        execution_context = get_authenticated_execution_context(customer_id)

        self.assertEqual(execution_context.customer_id, 123)

    async def test_missing_authorization_header_is_rejected(self) -> None:
        request = Request({"type": "http", "headers": []})
        credentials = await bearer_scheme(request)
        self.assertIsNone(credentials)

        with self.assertRaises(HTTPException) as error:
            get_current_customer(credentials)

        self.assertEqual(error.exception.status_code, 401)

    def test_malformed_token_is_rejected(self) -> None:
        self.assert_unauthorized("not-a-jwt")

    def test_expired_token_is_rejected(self) -> None:
        token = jwt.encode(
            {
                "sub": "123",
                "iat": int((datetime.now(timezone.utc) - timedelta(minutes=2)).timestamp()),
                "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        self.assert_unauthorized(token)

    def test_invalid_signature_is_rejected(self) -> None:
        token = jwt.encode(
            {"sub": "123", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "different-test-secret-long-enough-to-pass-validation",
            algorithm=settings.JWT_ALGORITHM,
        )
        self.assert_unauthorized(token)

    def test_invalid_customer_id_claim_is_rejected(self) -> None:
        token = jwt.encode(
            {
                "sub": "not-an-integer",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        self.assert_unauthorized(token)

    def assert_unauthorized(self, token: str) -> None:
        with self.assertRaises(HTTPException) as error:
            get_current_customer(self.credentials(token))

        self.assertEqual(error.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
