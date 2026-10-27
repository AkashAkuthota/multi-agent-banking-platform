"""Targeted redaction for common identifiers in free-text LLM inputs."""

import re

_EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_NUMBER_PATTERN = re.compile(r"(?<!\w)\+?[\d(][\d\s().-]{6,}\d(?!\w)")


def redact_sensitive_text(text: str) -> str:
    """Redact email, SSN, and long phone/account-like digit sequences."""
    redacted = _EMAIL_PATTERN.sub("[REDACTED]", text)
    redacted = _SSN_PATTERN.sub("[REDACTED]", redacted)
    return _NUMBER_PATTERN.sub(_redact_long_number, redacted)


def account_number_last4(account_number: str) -> str:
    """Return the non-sensitive display suffix for an account number.

    Account numbers must remain inside the persistence layer. Public API and
    MCP responses can use this value for human recognition without exposing
    the full identifier.
    """
    return account_number[-4:]


def _redact_long_number(match: re.Match[str]) -> str:
    value = match.group()
    digit_count = sum(character.isdigit() for character in value)
    return "[REDACTED]" if digit_count >= 8 else value
