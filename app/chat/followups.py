"""Deterministic follow-up resolution from encrypted session state."""

from __future__ import annotations

import re

from app.llm.schemas import CommandAction, Intent
from app.services.chat_session import ChatSessionState, SessionAccount

_OTHER_ACCOUNT = re.compile(
    r"\b(?:the\s+other\s+(?:account|one)|other\s+account)\b",
    re.IGNORECASE,
)
_SAME_ACCOUNT = re.compile(
    r"\b(?:that\s+account|the\s+same\s+account|this\s+account|"
    r"same\s+(?:account|one)|same\s+as\s+last\s+time)\b",
    re.IGNORECASE,
)
_ACCOUNT_TYPE_REF = (
    (re.compile(r"\b(?:my\s+)?savings(?:\s+account)?\b", re.IGNORECASE), "SAVINGS"),
    (re.compile(r"\b(?:my\s+)?current(?:\s+account)?\b", re.IGNORECASE), "CURRENT"),
)
_EXPLICIT_ACCOUNT_ID = re.compile(r"\bACC_[A-Z0-9_]+\b")


def apply_session_context(
    intent: Intent,
    user_message: str,
    state: ChatSessionState,
) -> Intent:
    """Fill missing command fields from session state. Never invent a confirm ID."""
    action = intent.action
    if action is None:
        return intent
    if action.kind in {"confirm", "cancel"}:
        return intent
    if action.kind == "daily_limit_change":
        return intent.model_copy(
            update={
                "action": _resolve_daily_limit_action(action, user_message, state),
            }
        )
    if action.kind == "internal_transfer":
        return intent.model_copy(
            update={
                "action": _resolve_transfer_action(action, user_message, state),
            }
        )
    return intent


def resolve_account_reference(
    user_message: str,
    state: ChatSessionState,
) -> str | None:
    """Resolve a pronoun or type mention to exactly one previously listed account."""
    explicit = _EXPLICIT_ACCOUNT_ID.search(user_message)
    if explicit is not None:
        return explicit.group(0)

    if _OTHER_ACCOUNT.search(user_message):
        return _other_account_id(state)

    if _SAME_ACCOUNT.search(user_message):
        return state.last_referenced_account_id

    for pattern, account_type in _ACCOUNT_TYPE_REF:
        if pattern.search(user_message):
            return _unique_account_id_for_type(state.accounts, account_type)
    return None


def accounts_from_tool_result(tool_result: dict[str, object]) -> list[SessionAccount]:
    """Keep active account references from MCP output, never full account numbers."""
    rows = tool_result.get("result")
    if not isinstance(rows, list):
        return []
    accounts: list[SessionAccount] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_id = row.get("account_id")
        account_type = row.get("account_type")
        status = row.get("status")
        if (
            isinstance(account_id, str)
            and isinstance(account_type, str)
            and status == "ACTIVE"
        ):
            accounts.append(
                SessionAccount(account_id=account_id, account_type=account_type)
            )
    return sorted(accounts, key=lambda account: account.account_id)


def _resolve_daily_limit_action(
    action: CommandAction,
    user_message: str,
    state: ChatSessionState,
) -> CommandAction:
    account_id = action.account_id or resolve_account_reference(user_message, state)
    requested_limit = action.requested_limit
    if (
        requested_limit is None
        and state.last_clarification == "daily_limit_details"
        and state.last_requested_limit is not None
    ):
        requested_limit = state.last_requested_limit
    return action.model_copy(
        update={
            "account_id": account_id,
            "requested_limit": requested_limit,
        }
    )


def _resolve_transfer_action(
    action: CommandAction,
    user_message: str,
    state: ChatSessionState,
) -> CommandAction:
    resolved = resolve_account_reference(user_message, state)
    source = action.source_account_id
    destination = action.destination_account_id
    if source is None and destination is None and resolved is not None:
        source = resolved
    elif source is None and resolved is not None and resolved != destination:
        source = resolved
    elif destination is None and resolved is not None and resolved != source:
        destination = resolved
    return action.model_copy(
        update={
            "source_account_id": source,
            "destination_account_id": destination,
        }
    )


def _other_account_id(state: ChatSessionState) -> str | None:
    account_ids = [account.account_id for account in state.accounts]
    if len(account_ids) != 2:
        return None
    referenced = state.last_referenced_account_id
    if referenced not in account_ids:
        return None
    return next(account_id for account_id in account_ids if account_id != referenced)


def _unique_account_id_for_type(
    accounts: list[SessionAccount],
    account_type: str,
) -> str | None:
    matches = [
        account.account_id
        for account in accounts
        if account.account_type == account_type
    ]
    if len(matches) != 1:
        return None
    return matches[0]
