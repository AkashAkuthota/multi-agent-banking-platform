"""Allowlisted presentation data for final response generation."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents.coordinator import (
    AggregateExecutionResult,
    AggregateExecutionStatus,
    AgentDomain,
    DomainExecutionResult,
    DomainExecutionStatus,
    IntentOutcome,
)
from app.core.pii import redact_sensitive_text


class _PresentationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AccountPresentation(_PresentationModel):
    account_type: str
    balance: str
    currency: str
    status: str


class TransactionPresentation(_PresentationModel):
    transaction_type: str
    amount: str
    currency: str
    merchant: str | None
    category: str | None
    transaction_date: str
    status: str
    description: str | None


class ServiceRequestPresentation(_PresentationModel):
    request_type: str
    status: str
    created_at: str
    completed_at: str | None


class AccountsPresentationData(_PresentationModel):
    domain: Literal["accounts"] = "accounts"
    accounts: list[AccountPresentation]


class TransactionsPresentationData(_PresentationModel):
    domain: Literal["transactions"] = "transactions"
    transactions: list[TransactionPresentation]


class ServiceRequestsPresentationData(_PresentationModel):
    domain: Literal["service_requests"] = "service_requests"
    service_requests: list[ServiceRequestPresentation]


PresentationData: TypeAlias = Annotated[
    AccountsPresentationData
    | TransactionsPresentationData
    | ServiceRequestsPresentationData,
    Field(discriminator="domain"),
]

_DOMAIN_ORDER: tuple[AgentDomain, ...] = (
    "accounts",
    "transactions",
    "service_requests",
)


class DomainPresentationResult(_PresentationModel):
    """Sanitized result and outcome for one requested domain."""

    domain: AgentDomain
    status: DomainExecutionStatus
    data: PresentationData | None
    failure_code: Literal["operation_failed"] | None = None

    @model_validator(mode="after")
    def validate_result_status(self) -> "DomainPresentationResult":
        if self.status == "failed":
            if self.data is not None or self.failure_code != "operation_failed":
                raise ValueError("failed result must contain only a safe failure code")
            return self

        if self.data is None or self.data.domain != self.domain:
            raise ValueError("successful result requires matching domain data")
        if self.failure_code is not None:
            raise ValueError("successful result cannot contain a failure code")

        row_count = _presentation_row_count(self.data)
        if self.status == "empty" and row_count != 0:
            raise ValueError("empty result must contain no records")
        if self.status == "succeeded" and row_count == 0:
            raise ValueError("succeeded result must contain records")
        return self


class AggregatePresentationData(_PresentationModel):
    """Canonical, sanitized presentation data for a coordinator aggregate."""

    outcome: IntentOutcome
    status: AggregateExecutionStatus
    results: list[DomainPresentationResult]

    @model_validator(mode="after")
    def validate_aggregate(self) -> "AggregatePresentationData":
        domains = [result.domain for result in self.results]
        if len(set(domains)) != len(domains):
            raise ValueError("aggregate cannot contain duplicate domains")
        if domains != sorted(domains, key=_DOMAIN_ORDER.index):
            raise ValueError("aggregate domains must be in canonical order")

        if self.outcome != "supported":
            if self.status != "not_dispatched" or self.results:
                raise ValueError("non-supported aggregate must not contain results")
            return self

        if not self.results:
            raise ValueError("supported aggregate must contain domain results")

        failed_count = sum(result.status == "failed" for result in self.results)
        expected_status: AggregateExecutionStatus
        if failed_count == len(self.results):
            expected_status = "failed"
        elif failed_count:
            expected_status = "partial_failure"
        else:
            expected_status = "succeeded"
        if self.status != expected_status:
            raise ValueError("aggregate status does not match domain results")
        return self


AggregatePresentation: TypeAlias = AggregatePresentationData
ResponsePresentationData: TypeAlias = PresentationData | AggregatePresentationData


class PresentationDataError(ValueError):
    """Raised when MCP output cannot be safely shaped for presentation."""


def build_presentation_data(
    domain: AgentDomain,
    tool_result: dict[str, object],
) -> PresentationData:
    """Select presentation-safe fields from serialized MCP output only."""
    rows = _get_result_rows(tool_result)
    try:
        if domain == "accounts":
            return AccountsPresentationData(
                accounts=[
                    AccountPresentation.model_validate(
                        _select_fields(
                            row,
                            ("account_type", "balance", "currency", "status"),
                        )
                    )
                    for row in rows
                ]
            )
        if domain == "transactions":
            return TransactionsPresentationData(
                transactions=[
                    TransactionPresentation.model_validate(
                        _redact_free_text_fields(
                            _select_fields(
                                row,
                                (
                                    "transaction_type",
                                    "amount",
                                    "currency",
                                    "merchant",
                                    "category",
                                    "transaction_date",
                                    "status",
                                    "description",
                                ),
                            ),
                            ("merchant", "category", "description"),
                        )
                    )
                    for row in rows
                ]
            )
        return ServiceRequestsPresentationData(
            service_requests=[
                ServiceRequestPresentation.model_validate(
                    _select_fields(
                        row,
                        ("request_type", "status", "created_at", "completed_at"),
                    )
                )
                for row in rows
            ]
        )
    except ValidationError as error:
        raise PresentationDataError(
            "MCP output did not match the expected banking result"
        ) from error


def build_aggregate_presentation_data(
    aggregate: AggregateExecutionResult,
) -> AggregatePresentationData:
    """Sanitize each coordinator domain result independently."""
    if not isinstance(aggregate, AggregateExecutionResult):
        raise PresentationDataError(
            "Expected a coordinator aggregate result"
        )

    try:
        results = [
            _build_domain_presentation_result(result)
            for result in aggregate.results
        ]
        return AggregatePresentationData(
            outcome=aggregate.outcome,
            status=aggregate.status,
            results=results,
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise PresentationDataError(
            "Coordinator aggregate did not match the expected result shape"
        ) from error


def _build_domain_presentation_result(
    result: DomainExecutionResult,
) -> DomainPresentationResult:
    if not isinstance(result, DomainExecutionResult):
        raise TypeError("Aggregate contains an invalid domain result")

    if result.status == "failed":
        if result.data is not None:
            raise ValueError("Failed domain result cannot contain data")
        return DomainPresentationResult(
            domain=result.domain,
            status=result.status,
            data=None,
            failure_code=result.failure_code,
        )

    if not isinstance(result.data, dict):
        raise TypeError("Successful domain result must contain structured data")
    return DomainPresentationResult(
        domain=result.domain,
        status=result.status,
        data=build_presentation_data(result.domain, result.data),
        failure_code=result.failure_code,
    )


def _presentation_row_count(data: PresentationData) -> int:
    if isinstance(data, AccountsPresentationData):
        return len(data.accounts)
    if isinstance(data, TransactionsPresentationData):
        return len(data.transactions)
    return len(data.service_requests)


def _get_result_rows(tool_result: dict[str, object]) -> list[dict[str, object]]:
    result = tool_result.get("result")
    if not isinstance(result, list) or not all(
        isinstance(row, dict) for row in result
    ):
        raise PresentationDataError(
            "MCP output did not contain serialized result records"
        )
    return result


def _select_fields(
    record: dict[str, object],
    fields: tuple[str, ...],
) -> dict[str, object]:
    return {field: record[field] for field in fields if field in record}


def _redact_free_text_fields(
    record: dict[str, object],
    fields: tuple[str, ...],
) -> dict[str, object]:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str):
            record[field] = redact_sensitive_text(value)
    return record
