"""Validated structured output for banking request classification."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IntentOutcome = Literal["supported", "unknown", "ambiguous", "unsupported"]
IntentDomain = Literal["accounts", "transactions", "service_requests"]
CommandKind = Literal[
    "daily_limit_change",
    "internal_transfer",
    "service_request",
    "confirm",
    "cancel",
]
_DOMAIN_ORDER: dict[IntentDomain, int] = {
    "accounts": 0,
    "transactions": 1,
    "service_requests": 2,
}
_COMMAND_FIELDS: dict[CommandKind, frozenset[str]] = {
    "daily_limit_change": frozenset({"account_id", "requested_limit"}),
    "internal_transfer": frozenset(
        {"source_account_id", "destination_account_id", "amount"}
    ),
    "service_request": frozenset({"request_type"}),
    "confirm": frozenset({"request_id"}),
    "cancel": frozenset({"request_id"}),
}
_ALL_COMMAND_FIELDS = frozenset().union(*_COMMAND_FIELDS.values())


class CommandAction(BaseModel):
    """Untrusted command fields extracted from chat, never an authorization."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: CommandKind
    account_id: str | None = Field(default=None, min_length=1, max_length=20)
    requested_limit: str | None = Field(default=None, min_length=1, max_length=32)
    source_account_id: str | None = Field(default=None, min_length=1, max_length=20)
    destination_account_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    amount: str | None = Field(default=None, min_length=1, max_length=32)
    request_type: str | None = Field(default=None, min_length=1, max_length=30)
    request_id: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        ),
    )

    @model_validator(mode="after")
    def reject_fields_for_other_commands(self) -> "CommandAction":
        allowed_fields = _COMMAND_FIELDS[self.kind]
        for field_name in _ALL_COMMAND_FIELDS:
            if field_name not in allowed_fields and getattr(self, field_name) is not None:
                raise ValueError(
                    f"{field_name} is not valid for command kind {self.kind}"
                )
        return self


class Intent(BaseModel):
    """A validated request classification, not an execution or authorization plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: IntentOutcome
    domains: list[IntentDomain]
    action: CommandAction | None = None

    @model_validator(mode="after")
    def validate_outcome_and_domains(self) -> "Intent":
        if len(set(self.domains)) != len(self.domains):
            raise ValueError("domains must not contain duplicates")

        if self.action is not None:
            if self.outcome != "supported" or self.domains:
                raise ValueError(
                    "command intent must be supported and cannot include read domains"
                )
        elif self.outcome == "supported" and not self.domains:
            raise ValueError("supported intent requires a domain or command action")
        elif self.outcome != "supported" and self.domains:
            raise ValueError("non-supported intent cannot contain domains")

        self.domains.sort(key=_DOMAIN_ORDER.__getitem__)
        return self
