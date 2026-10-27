"""Intent classification followed by deterministic coordinator dispatch."""

import re
from typing import Protocol

from app.core.pii import redact_sensitive_text
from app.llm.schemas import CommandAction, CommandKind, Intent


_DAILY_LIMIT_COMMAND = re.compile(
    r"\b(?:increase|raise|change|set|decrease|lower|reduce|update)\b"
    r"[\s\S]*\bdaily\s+limit\b"
    r"|\bdaily\s+limit\b[\s\S]*\b(?:increase|raise|change|set|decrease|lower|reduce|update)\b",
    re.IGNORECASE,
)
_DAILY_LIMIT_AMOUNT = re.compile(
    r"\b(?:to|at)\s*(?:inr\s*)?(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_ACCOUNT_ID = re.compile(r"\bACC_[A-Z0-9_]+\b")
_PROPOSAL_DECISION = re.compile(
    r"^\s*(confirm|cancel)\b(?:\s+proposal)?\s*"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?",
    re.IGNORECASE,
)
_INTERNAL_TRANSFER = re.compile(
    r"\b(?:transfer|move|send)\b[\s\S]*?\bfrom\s+"
    r"(ACC_[A-Z0-9_]+)\s+to\s+(ACC_[A-Z0-9_]+)\b",
    re.IGNORECASE,
)
_TRANSFER_AMOUNT = re.compile(
    r"\b(?:transfer|move|send)\s+(?:inr\s*)?(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_SERVICE_REQUEST = re.compile(
    r"\b(?:request|order|replace|send|issue|get)\b[\s\S]*?\b"
    r"(replacement\s+card|cheque\s+book|statement)\b",
    re.IGNORECASE,
)
_SERVICE_REQUEST_TYPES = {
    "replacement card": "CARD_REPLACEMENT",
    "cheque book": "CHEQUE_BOOK",
    "statement": "STATEMENT",
}


class IntentProvider(Protocol):
    async def classify(self, user_message: str) -> Intent: ...


class IntentRouter:
    """Classify a message without receiving execution or identity context."""

    def __init__(self, intent_provider: IntentProvider) -> None:
        self._intent_provider = intent_provider

    async def classify(self, user_message: str) -> Intent:
        """Return a validated domain decision for the application to dispatch."""
        redacted_message = redact_sensitive_text(user_message)
        command_intent = _explicit_command_intent(redacted_message)
        if command_intent is not None:
            return command_intent
        return await self._intent_provider.classify(redacted_message)


def _explicit_command_intent(user_message: str) -> Intent | None:
    """Parse explicit commands before model classification for reliable safety gates."""
    decision_match = _PROPOSAL_DECISION.search(user_message)
    if decision_match is not None:
        return _command_intent(
            decision_match.group(1).lower(),
            request_id=decision_match.group(2),
        )

    if _DAILY_LIMIT_COMMAND.search(user_message):
        amount_match = _DAILY_LIMIT_AMOUNT.search(user_message)
        account_match = _ACCOUNT_ID.search(user_message)
        return _command_intent(
            "daily_limit_change",
            account_id=(account_match.group(0) if account_match else None),
            requested_limit=(amount_match.group(1) if amount_match else None),
        )

    transfer_match = _INTERNAL_TRANSFER.search(user_message)
    if transfer_match is not None:
        amount_match = _TRANSFER_AMOUNT.search(user_message)
        return _command_intent(
            "internal_transfer",
            source_account_id=transfer_match.group(1),
            destination_account_id=transfer_match.group(2),
            amount=(amount_match.group(1) if amount_match else None),
        )

    service_match = _SERVICE_REQUEST.search(user_message)
    if service_match is not None:
        request_type = _SERVICE_REQUEST_TYPES[service_match.group(1).lower()]
        return _command_intent("service_request", request_type=request_type)

    return None


def _command_intent(kind: CommandKind, **fields: str | None) -> Intent:
    """Create an untrusted command intent for the deterministic dispatcher."""
    return Intent(
        outcome="supported",
        domains=[],
        action=CommandAction(kind=kind, **fields),
    )
