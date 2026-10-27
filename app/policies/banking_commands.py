"""Deterministic policies for sandbox banking commands."""

from decimal import Decimal, InvalidOperation

from app.policies import PolicyViolationError

MAX_TRANSFER_AMOUNT = Decimal("50000.00")
SERVICE_REQUEST_TYPES = frozenset(
    {"STATEMENT", "CHEQUE_BOOK", "CARD_REPLACEMENT"}
)


class BankingCommandPolicyError(PolicyViolationError):
    """A transfer or service-request proposal violates policy."""


def validate_transfer_amount(amount: Decimal) -> Decimal:
    """Require a positive, two-decimal transfer within the sandbox cap."""
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise BankingCommandPolicyError("Transfer amount must be a finite decimal")
    try:
        if amount != amount.quantize(Decimal("0.01")):
            raise BankingCommandPolicyError(
                "Transfer amount cannot have more than two decimals"
            )
    except InvalidOperation as error:
        raise BankingCommandPolicyError("Transfer amount is invalid") from error
    if amount <= 0 or amount > MAX_TRANSFER_AMOUNT:
        raise BankingCommandPolicyError(
            "Transfer amount must be greater than zero and no more than 50000.00"
        )
    return amount


def validate_service_request_type(request_type: str) -> str:
    """Return a normalized allow-listed service request type."""
    normalized_type = request_type.strip().upper()
    if normalized_type not in SERVICE_REQUEST_TYPES:
        raise BankingCommandPolicyError("Unsupported service request type")
    return normalized_type
