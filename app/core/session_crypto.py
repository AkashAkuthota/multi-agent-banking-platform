"""AES-GCM helpers for customer-bound chat session payloads."""

import os
from base64 import urlsafe_b64decode

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SESSION_NONCE_SIZE = 12
SESSION_KEY_SIZE = 32


class SessionEncryptionError(RuntimeError):
    """Raised when a session payload cannot be encrypted or decrypted."""


def decode_session_encryption_key(value: str) -> bytes:
    """Decode a url-safe Base64 32-byte AES-GCM key."""
    try:
        key = urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise SessionEncryptionError("Session encryption key is invalid") from error
    if len(key) != SESSION_KEY_SIZE:
        raise SessionEncryptionError("Session encryption key must decode to 32 bytes")
    return key


def encrypt_session_payload(
    plaintext: bytes,
    key: bytes,
    *,
    associated_data: bytes,
    nonce: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Encrypt a session payload bound to associated data such as customer ID."""
    if len(key) != SESSION_KEY_SIZE:
        raise SessionEncryptionError("Session encryption key must be 32 bytes")
    nonce_bytes = nonce if nonce is not None else os.urandom(SESSION_NONCE_SIZE)
    if len(nonce_bytes) != SESSION_NONCE_SIZE:
        raise SessionEncryptionError("Session nonce must be 12 bytes")
    ciphertext = AESGCM(key).encrypt(nonce_bytes, plaintext, associated_data)
    return nonce_bytes, ciphertext


def decrypt_session_payload(
    nonce: bytes,
    ciphertext: bytes,
    key: bytes,
    *,
    associated_data: bytes,
) -> bytes:
    """Decrypt a session payload and fail closed on authentication errors."""
    if len(key) != SESSION_KEY_SIZE or len(nonce) != SESSION_NONCE_SIZE:
        raise SessionEncryptionError("Session ciphertext could not be decrypted")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, associated_data)
    except (InvalidTag, ValueError) as error:
        raise SessionEncryptionError("Session ciphertext could not be decrypted") from error
