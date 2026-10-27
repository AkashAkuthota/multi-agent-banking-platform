"""Request and response schemas for authenticated banking chat."""

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

ChatDomain = Literal["accounts", "transactions", "service_requests"]
_DOMAIN_ORDER = {
    "accounts": 0,
    "transactions": 1,
    "service_requests": 2,
}


class ChatRequest(BaseModel):
    """An authenticated natural-language read, command, or explicit decision."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, message: str) -> str:
        if not message.strip():
            raise ValueError("Message must not be blank")
        return message


class ChatCommandResponse(BaseModel):
    """Safe, deterministic summary of a sandbox command state transition."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["daily_limit_change", "internal_transfer", "service_request"]
    action: Literal["proposal", "confirm", "cancel"]
    request_id: UUID
    status: Literal[
        "PENDING",
        "CONFIRMED",
        "CANCELLED",
        "EXPIRED",
        "CONFLICTED",
    ]
    applied: bool
    expires_at: str | None = None


class ChatAccountOption(BaseModel):
    """Safe account reference offered while clarifying a command."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=20)
    account_type: str = Field(min_length=1, max_length=50)


class ChatPendingProposal(BaseModel):
    """The single in-session proposal that dedicated confirm/cancel actions may use."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    kind: Literal["daily_limit_change", "internal_transfer", "service_request"]
    expires_at: str | None = None


class ChatResponse(BaseModel):
    """Validated read answer, command proposal/action result, or clarification."""

    model_config = ConfigDict(extra="forbid")

    domain: ChatDomain | None = None
    domains: list[ChatDomain] | None = None
    answer: str
    command: ChatCommandResponse | None = None
    account_options: list[ChatAccountOption] | None = None
    clarification: Literal[
        "daily_limit_details",
        "transfer_details",
        "service_request_type",
        "proposal_id",
    ] | None = None
    pending_proposal: ChatPendingProposal | None = None

    @model_validator(mode="after")
    def validate_domains(self) -> "ChatResponse":
        is_read_response = self.domain is not None or self.domains is not None
        response_kinds = sum(
            (is_read_response, self.command is not None, self.clarification is not None)
        )
        if response_kinds != 1:
            raise ValueError(
                "provide exactly one read result, command result, or clarification"
            )
        if (
            is_read_response
            and (self.domain is None) == (self.domains is None)
        ):
            raise ValueError("provide either one domain or multiple domains")
        if self.domains is not None:
            if len(self.domains) < 2 or len(set(self.domains)) != len(self.domains):
                raise ValueError("multiple domains must be distinct")
            if self.domains != sorted(self.domains, key=_DOMAIN_ORDER.__getitem__):
                raise ValueError("multiple domains must use canonical order")
        if self.account_options is not None and self.clarification != "daily_limit_details":
            raise ValueError(
                "account options are only valid for a daily-limit clarification"
            )
        return self
