"""Tests for deterministic chat follow-up resolution."""

import unittest
from uuid import UUID

from app.chat.followups import apply_session_context, resolve_account_reference
from app.llm.schemas import CommandAction, Intent
from app.services.chat_session import ChatSessionState, SessionAccount


def _two_accounts() -> ChatSessionState:
    return ChatSessionState(
        accounts=[
            SessionAccount(account_id="ACC_001", account_type="CURRENT"),
            SessionAccount(account_id="ACC_DEMO_002", account_type="SAVINGS"),
        ],
        last_referenced_account_id="ACC_001",
        last_requested_limit="20000",
        last_clarification="daily_limit_details",
    )


class FollowUpResolutionTests(unittest.TestCase):
    def test_other_account_resolves_to_the_unreferenced_account(self) -> None:
        self.assertEqual(
            resolve_account_reference("use the other account", _two_accounts()),
            "ACC_DEMO_002",
        )

    def test_same_as_last_time_uses_last_referenced_account(self) -> None:
        self.assertEqual(
            resolve_account_reference("same as last time", _two_accounts()),
            "ACC_001",
        )

    def test_confirm_intent_never_receives_a_session_proposal_id(self) -> None:
        pending = UUID("66b74620-aefb-42ae-9c94-796a2120991c")
        state = ChatSessionState(
            pending_proposal_id=pending,
            pending_proposal_kind="daily_limit_change",
            pending_proposal_expires_at="2099-01-01T00:00:00+00:00",
        )
        intent = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(kind="confirm"),
        )

        resolved = apply_session_context(intent, "yes", state)

        self.assertIsNone(resolved.action.request_id)

    def test_daily_limit_follow_up_reuses_session_amount_and_other_account(
        self,
    ) -> None:
        intent = Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(kind="daily_limit_change"),
        )

        resolved = apply_session_context(
            intent,
            "do it on the other account",
            _two_accounts(),
        )

        self.assertEqual(resolved.action.account_id, "ACC_DEMO_002")
        self.assertEqual(resolved.action.requested_limit, "20000")


if __name__ == "__main__":
    unittest.main()
