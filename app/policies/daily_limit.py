"""Deterministic policy for sandbox daily-limit changes."""

from decimal import Decimal, InvalidOperation

from app.policies import PolicyViolationError


MIN_DAILY_LIMIT = Decimal("10000.00")
MAX_DAILY_LIMIT = Decimal("50000.00")


class DailyLimitPolicyError(PolicyViolationError):
    """A daily-limit request violates the deterministic policy."""


def validate_daily_limit(value: Decimal) -> Decimal:
    """Validate a limit without lossy conversion or rounding."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DailyLimitPolicyError("The daily limit must be a finite decimal amount")
    try:
        if value != value.quantize(Decimal("0.01")):
            raise DailyLimitPolicyError("The daily limit cannot have more than two decimals")
    except InvalidOperation as error:
        raise DailyLimitPolicyError("The daily limit amount is invalid") from error
    if not MIN_DAILY_LIMIT <= value <= MAX_DAILY_LIMIT:
        raise DailyLimitPolicyError(
            "The daily limit must be between 10000.00 and 50000.00"
        )
    return value


def validate_active_account(status: str) -> None:
    """Reject changes to accounts that are not active."""
    if status != "ACTIVE":
        raise DailyLimitPolicyError("Only active accounts can change daily limits")
