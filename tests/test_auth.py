"""Tests for the login endpoint."""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.auth import get_me, login
from app.core.login_rate_limit import LoginAttemptLimiter
from app.models import Customer, CustomerCredential
from app.schemas.auth import LoginRequest


class LoginEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = AsyncMock()
        self.customer = Customer(id=42, email="customer@example.com")
        self.customer.credential = CustomerCredential(
            customer_id=42,
            password_hash="stored-hash",
            is_active=True,
        )

    def install_limiter(self, max_attempts: int) -> None:
        limiter = LoginAttemptLimiter(max_attempts=max_attempts, window_seconds=60)
        patcher = patch("app.api.auth.login_attempt_limiter", limiter)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_login_accepts_valid_credentials(self) -> None:
        self.session.scalar.return_value = self.customer
        with (
            patch("app.api.auth.verify_password", return_value=True),
            patch("app.api.auth.create_access_token", return_value="signed-token") as token,
        ):
            response = await login(
                LoginRequest(email="customer@example.com", password="correct"),
                self.session,
            )

        self.assertEqual(response.access_token, "signed-token")
        self.assertEqual(response.token_type, "bearer")
        token.assert_called_once_with(42)

    async def test_login_rejects_wrong_password(self) -> None:
        self.session.scalar.return_value = self.customer
        with patch("app.api.auth.verify_password", return_value=False):
            with self.assertRaises(HTTPException) as error:
                await login(
                    LoginRequest(email="customer@example.com", password="wrong"),
                    self.session,
                )

        self.assert_invalid_credentials(error.exception)

    async def test_login_rejects_unknown_email(self) -> None:
        self.session.scalar.return_value = None
        with patch("app.api.auth.verify_password") as verify:
            with self.assertRaises(HTTPException) as error:
                await login(
                    LoginRequest(email="unknown@example.com", password="password"),
                    self.session,
                )

        self.assert_invalid_credentials(error.exception)
        verify.assert_not_called()

    async def test_login_rejects_inactive_credential(self) -> None:
        self.customer.credential.is_active = False
        self.session.scalar.return_value = self.customer
        with patch("app.api.auth.verify_password") as verify:
            with self.assertRaises(HTTPException) as error:
                await login(
                    LoginRequest(email="customer@example.com", password="password"),
                    self.session,
                )

        self.assert_invalid_credentials(error.exception)
        verify.assert_not_called()

    async def test_login_attempts_are_rate_limited_per_normalized_email(self) -> None:
        self.install_limiter(max_attempts=1)
        self.session.scalar.return_value = self.customer
        with patch("app.api.auth.verify_password", return_value=False):
            with self.assertRaises(HTTPException) as first_error:
                await login(
                    LoginRequest(email="Customer@Example.com", password="wrong"),
                    self.session,
                )
            with self.assertRaises(HTTPException) as rate_limit_error:
                await login(
                    LoginRequest(email="customer@example.com", password="wrong"),
                    self.session,
                )

        self.assert_invalid_credentials(first_error.exception)
        self.assertEqual(rate_limit_error.exception.status_code, 429)
        self.assertIn("Retry-After", rate_limit_error.exception.headers)

    async def test_successful_login_clears_reserved_attempts(self) -> None:
        self.install_limiter(max_attempts=2)
        self.session.scalar.return_value = self.customer
        with patch("app.api.auth.verify_password", side_effect=[False, True, False]):
            with self.assertRaises(HTTPException):
                await login(
                    LoginRequest(email="customer@example.com", password="wrong"),
                    self.session,
                )
            await login(
                LoginRequest(email="customer@example.com", password="correct"),
                self.session,
            )
            with self.assertRaises(HTTPException) as error:
                await login(
                    LoginRequest(email="customer@example.com", password="wrong"),
                    self.session,
                )

        self.assert_invalid_credentials(error.exception)

    def assert_invalid_credentials(self, error: HTTPException) -> None:
        self.assertEqual(error.status_code, 401)
        self.assertEqual(error.detail, "Invalid credentials")


class CurrentCustomerEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_me_returns_dependency_customer_id(self) -> None:
        response = await get_me(42)

        self.assertEqual(response, {"customer_id": 42})


if __name__ == "__main__":
    unittest.main()
