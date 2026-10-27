"""Typed state primitives for the banking LangGraph workflow."""

from collections.abc import Mapping
from typing import Annotated, Literal, NotRequired, TypedDict

from app.llm.schemas import Intent, IntentDomain

DomainResultStatus = Literal["succeeded", "empty", "failed"]
GraphExecutionStatus = Literal[
    "pending",
    "not_dispatched",
    "succeeded",
    "partial_failure",
    "failed",
]
_SUPPORTED_DOMAINS: tuple[IntentDomain, ...] = (
    "accounts",
    "transactions",
    "service_requests",
)
_DOMAIN_ORDER = {
    domain: index for index, domain in enumerate(_SUPPORTED_DOMAINS)
}
_DOMAIN_RESULT_STATUSES = ("succeeded", "empty", "failed")
_GRAPH_STATUSES = (
    "pending",
    "not_dispatched",
    "succeeded",
    "partial_failure",
    "failed",
)


class DomainExecutionState(TypedDict):
    """Structured, identity-free result recorded for one banking domain."""

    status: DomainResultStatus
    data: dict[str, object] | None
    failure_code: NotRequired[Literal["operation_failed"]]


class GraphStateValidationError(ValueError):
    """Raised when untrusted or malformed input does not match graph state."""


def merge_domain_results(
    current: dict[IntentDomain, DomainExecutionState],
    update: dict[IntentDomain, DomainExecutionState],
) -> dict[IntentDomain, DomainExecutionState]:
    """Merge disjoint domain updates; duplicate or unsupported domains fail."""
    if not isinstance(current, Mapping) or not isinstance(update, Mapping):
        raise GraphStateValidationError("Domain results must be mappings")

    merged: dict[IntentDomain, DomainExecutionState] = {}
    for results in (current, update):
        for domain, result in results.items():
            if domain not in _SUPPORTED_DOMAINS:
                raise GraphStateValidationError("Unsupported result domain")
            if domain in merged:
                raise GraphStateValidationError("Duplicate domain result")
            merged[domain] = _validate_domain_result(result)

    return dict(sorted(merged.items(), key=lambda item: _DOMAIN_ORDER[item[0]]))


class BankingGraphState(TypedDict, total=False):
    """Graph state excludes authentication context and other trusted identity."""

    intent: Intent
    domain_results: Annotated[
        dict[IntentDomain, DomainExecutionState],
        merge_domain_results,
    ]
    execution_status: GraphExecutionStatus
    graph_status: Literal["completed"]


def validate_graph_state(state: Mapping[str, object]) -> Intent:
    """Validate runtime graph input that TypedDict alone cannot enforce."""
    if not isinstance(state, Mapping):
        raise GraphStateValidationError("Graph state must be a mapping")
    if "intent" not in state:
        raise GraphStateValidationError("Graph state requires an intent")

    raw_intent = state["intent"]
    try:
        if isinstance(raw_intent, Intent):
            intent = Intent.model_validate(raw_intent.model_dump())
        else:
            intent = Intent.model_validate(raw_intent)
    except (TypeError, ValueError) as error:
        raise GraphStateValidationError("Graph state intent is invalid") from error

    if "domain_results" in state:
        raw_results = state["domain_results"]
        if not isinstance(raw_results, Mapping):
            raise GraphStateValidationError("Domain results must be a mapping")
        merge_domain_results({}, raw_results)  # type: ignore[arg-type]

    if "execution_status" in state:
        if state["execution_status"] not in _GRAPH_STATUSES:
            raise GraphStateValidationError("Graph execution status is invalid")

    if "graph_status" in state and state["graph_status"] != "completed":
        raise GraphStateValidationError("Graph status is invalid")

    return intent


def _validate_domain_result(value: object) -> DomainExecutionState:
    if not isinstance(value, Mapping):
        raise GraphStateValidationError("Domain result must be a mapping")

    allowed_fields = {"status", "data", "failure_code"}
    if not set(value).issubset(allowed_fields):
        raise GraphStateValidationError("Domain result contains unexpected fields")

    status = value.get("status")
    data = value.get("data")
    failure_code = value.get("failure_code")
    if status not in _DOMAIN_RESULT_STATUSES:
        raise GraphStateValidationError("Domain result status is invalid")

    if status == "failed":
        if data is not None or failure_code != "operation_failed":
            raise GraphStateValidationError("Failed result is not safely shaped")
        return {
            "status": "failed",
            "data": None,
            "failure_code": "operation_failed",
        }

    if not isinstance(data, dict) or failure_code is not None:
        raise GraphStateValidationError("Successful result is not safely shaped")

    return {
        "status": status,
        "data": data,
    }
