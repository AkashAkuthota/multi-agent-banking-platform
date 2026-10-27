"""Tests for JWT access-token utilities."""

import unittest
from unittest.mock import patch

import jwt

from app.core.config import settings
from app.core.jwt import create_access_token, decode_access_token


class AccessTokenTests(unittest.TestCase):
    def test_create_decode_and_reject_invalid_token(self) -> None:
        with (
            patch.object(settings, "JWT_SECRET_KEY", "test-secret-key-for-jwt-tests-is-long-enough"),
            patch.object(settings, "JWT_ALGORITHM", "HS256"),
            patch.object(settings, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 5),
        ):
            token = create_access_token(123)
            payload = decode_access_token(token)

            self.assertEqual(payload["sub"], "123")
            self.assertIn("exp", payload)
            self.assertIn("iat", payload)
            with self.assertRaises(jwt.InvalidTokenError):
                decode_access_token("not-a-valid-token")


if __name__ == "__main__":
    unittest.main()
