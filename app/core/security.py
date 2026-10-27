"""Password hashing utilities."""

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a password using the recommended Argon2 configuration."""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against its encoded hash."""
    return password_hasher.verify(password, password_hash)
