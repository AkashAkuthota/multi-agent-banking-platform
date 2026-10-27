"""Customer-scoped chat session state, encrypted at rest."""

from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from app.core.config import settings
from app.core.session_crypto import (
    SessionEncryptionError,
    decode_session_encryption_key,
    decrypt_session_payload,
    encrypt_session_payload,
)
from app.models.chat_session import ChatSession

PendingProposalKind = Literal[
    "daily_limit_change",
    "internal_transfer",
    "service_request",
]
ChatClarification = Literal[
    "daily_limit_details",
    "transfer_details",
    "service_request_type",
    "proposal_id",
]


class SessionAccount(BaseModel):
    """Safe account reference retained for follow-up resolution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: str = Field(min_length=1, max_length=20)
    account_type: str = Field(min_length=1, max_length=50)


class ChatSessionState(BaseModel):
    """Application-level chat memory. Never contains raw message text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pending_proposal_id: UUID | None = None
    pending_proposal_kind: PendingProposalKind | None = None
    pending_proposal_expires_at: str | None = None
    accounts: list[SessionAccount] = Field(default_factory=list)
    last_referenced_account_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    last_requested_limit: str | None = Field(default=None, min_length=1, max_length=32)
    last_clarification: ChatClarification | None = None

    @model_validator(mode="after")
    def pending_proposal_fields_are_consistent(self) -> "ChatSessionState":
        has_id = self.pending_proposal_id is not None
        has_kind = self.pending_proposal_kind is not None
        if has_id != has_kind:
            raise ValueError("pending proposal ID and kind must be set together")
        return self

    def active_pending_proposal_id(
        self,
        *,
        now: datetime | None = None,
    ) -> UUID | None:
        """Return the stored proposal ID only while it is unexpired."""
        if self.pending_proposal_id is None:
            return None
        if self.pending_proposal_expires_at is None:
            return self.pending_proposal_id
        try:
            expires_at = datetime.fromisoformat(self.pending_proposal_expires_at)
        except ValueError:
            return None
        current = now or datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= current:
            return None
        return self.pending_proposal_id

    def clear_pending_proposal(self) -> None:
        """Drop the stored proposal after confirm, cancel, or expiry."""
        self.pending_proposal_id = None
        self.pending_proposal_kind = None
        self.pending_proposal_expires_at = None


class ChatSessionStore(Protocol):
    async def load(self, customer_id: int) -> ChatSessionState: ...

    async def save(self, customer_id: int, state: ChatSessionState) -> None: ...


class InMemoryChatSessionStore:
    """Process-local store used by tests. Not for deployment."""

    def __init__(self) -> None:
        self._states: dict[int, ChatSessionState] = {}

    async def load(self, customer_id: int) -> ChatSessionState:
        state = self._states.get(customer_id)
        if state is None:
            return ChatSessionState()
        return ChatSessionState.model_validate(state.model_dump())

    async def save(self, customer_id: int, state: ChatSessionState) -> None:
        self._states[customer_id] = ChatSessionState.model_validate(state.model_dump())


class EncryptedChatSessionStore:
    """Persist one AES-GCM session blob per JWT customer."""

    async def load(self, customer_id: int) -> ChatSessionState:
        from app.core.database import async_session

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            record = await session.scalar(
                select(ChatSession).where(ChatSession.customer_id == customer_id)
            )
            if record is None:
                return ChatSessionState()
            if record.expires_at <= now:
                await session.delete(record)
                await session.commit()
                return ChatSessionState()
            plaintext = decrypt_session_payload(
                record.nonce,
                record.ciphertext,
                _session_key(),
                associated_data=_associated_data(customer_id),
            )
            state = ChatSessionState.model_validate_json(plaintext)
            if state.active_pending_proposal_id(now=now) is None:
                state.clear_pending_proposal()
            return state

    async def save(self, customer_id: int, state: ChatSessionState) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=settings.CHAT_SESSION_TTL_MINUTES)
        state = ChatSessionState.model_validate(state.model_dump())
        if state.active_pending_proposal_id(now=now) is None:
            state.clear_pending_proposal()
        nonce, ciphertext = encrypt_session_payload(
            state.model_dump_json().encode("utf-8"),
            _session_key(),
            associated_data=_associated_data(customer_id),
        )
        from app.core.database import async_session

        async with async_session() as session:
            record = await session.scalar(
                select(ChatSession)
                .where(ChatSession.customer_id == customer_id)
                .with_for_update()
            )
            if record is None:
                session.add(
                    ChatSession(
                        customer_id=customer_id,
                        nonce=nonce,
                        ciphertext=ciphertext,
                        key_version=1,
                        created_at=now,
                        updated_at=now,
                        expires_at=expires_at,
                    )
                )
            else:
                record.nonce = nonce
                record.ciphertext = ciphertext
                record.updated_at = now
                record.expires_at = expires_at
            await session.commit()


def _session_key() -> bytes:
    try:
        return decode_session_encryption_key(
            settings.SESSION_ENCRYPTION_KEY.get_secret_value()
        )
    except SessionEncryptionError:
        raise


def _associated_data(customer_id: int) -> bytes:
    return f"customer:{customer_id}".encode("ascii")
