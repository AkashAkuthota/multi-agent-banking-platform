"""Tests for AES-GCM chat-session encryption."""

import unittest

from app.core.session_crypto import (
    SessionEncryptionError,
    decode_session_encryption_key,
    decrypt_session_payload,
    encrypt_session_payload,
)

_TEST_KEY = decode_session_encryption_key(
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)


class SessionCryptoTests(unittest.TestCase):
    def test_round_trip_is_bound_to_customer_associated_data(self) -> None:
        nonce, ciphertext = encrypt_session_payload(
            b'{"accounts":[]}',
            _TEST_KEY,
            associated_data=b"customer:1",
        )

        plaintext = decrypt_session_payload(
            nonce,
            ciphertext,
            _TEST_KEY,
            associated_data=b"customer:1",
        )

        self.assertEqual(plaintext, b'{"accounts":[]}')
        with self.assertRaises(SessionEncryptionError):
            decrypt_session_payload(
                nonce,
                ciphertext,
                _TEST_KEY,
                associated_data=b"customer:2",
            )

    def test_invalid_key_encoding_is_rejected(self) -> None:
        with self.assertRaises(SessionEncryptionError):
            decode_session_encryption_key("not-a-key")


if __name__ == "__main__":
    unittest.main()
