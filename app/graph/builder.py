"""LangGraph orchestration for authenticated banking requests."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agents.coordinator import (
    AggregateExecutionResult,
    AgentDomain,
    Coordinator,
    DomainExecutionResult,
)
from app.graph.state import (
    BankingGraphState,
    DomainExecutionState,
    GraphExecutionStatus,
    GraphStateValidationError,
    merge_domain_results,
    validate_graph_state,
)
from app.llm.schemas import Intent
from app.mcp.context import AuthenticatedExecutionContext


class _DomainDispatchState(TypedDict):
    domain: AgentDomain


def aggregate_execution_result(
    state: BankingGraphState,
) -> AggregateExecutionResult:
    """Convert validated graph output into the coordinator's public result."""
    intent = validate_graph_state(state)
    status = state.get("execution_status")
    if status in (None, "pending"):
        raise GraphStateValidationError("Graph execution did not complete")

    domain_results = state.get("domain_results", {})
    validated_results = merge_domain_results({}, domain_results)
    results = tuple(
        DomainExecutionResult(
            domain=domain,
            status=result["status"],
            data=result["data"],
            failure_code=result.get("failure_code"),
        )
        for domain, result in validated_results.items()
    )
    result_domains = [result.domain for result in results]
    if result_domains != intent.domains:
        if intent.outcome == "supported":
            raise GraphStateValidationError(
                "Graph results do not match the requested domains"
            )
        if result_domains:
            raise GraphStateValidationError(
                "Non-supported intent cannot contain domain results"
            )
    failed_count = sum(result.status == "failed" for result in results)
    expected_status: GraphExecutionStatus
    if intent.outcome != "supported":
        expected_status = "not_dispatched"
    elif failed_count == len(results):
        expected_status = "failed"
    elif failed_count:
        expected_status = "partial_failure"
    else:
        expected_status = "succeeded"
    if status != expected_status:
        raise GraphStateValidationError(
            "Graph execution status does not match its results"
        )
    return AggregateExecutionResult(
        outcome=intent.outcome,
        status=status,
        results=results,
    )


def build_graph(
    coordinator: Coordinator,
    execution_context: AuthenticatedExecutionContext,
):
    """Compile an authenticated workflow without putting identity in graph state."""
    graph = StateGraph(BankingGraphState)

    def initialize(state: BankingGraphState) -> dict[str, Intent | str]:
        intent = validate_graph_state(state)
        if state.get("domain_results", {}):
            raise GraphStateValidationError(
                "Domain results must be empty before execution"
            )
        if state.get("execution_status") not in (None, "pending"):
            raise GraphStateValidationError(
                "Graph execution must start in pending state"
            )
        return {"intent": intent, "execution_status": "pending"}

    def route_intent(
        state: BankingGraphState,
    ) -> str | list[Send]:
        intent = validate_graph_state(state)
        if intent.outcome != "supported":
            return "not_dispatched"
        coordinator.validate_domains(intent.domains)
        return [
            Send("execute_domain", {"domain": domain})
            for domain in intent.domains
        ]

    async def execute_domain(
        state: _DomainDispatchState,
    ) -> dict[str, dict[AgentDomain, DomainExecutionState]]:
        domain = state["domain"]
        result = await coordinator.execute_domain_result(
            domain,
            execution_context,
        )
        if not isinstance(result, DomainExecutionResult):
            raise GraphStateValidationError(
                "Coordinator returned an invalid domain result"
            )
        if result.domain != domain:
            raise GraphStateValidationError(
                "Coordinator result does not match the dispatched domain"
            )
        return {"domain_results": {domain: _domain_execution_state(result)}}

    def aggregate_results(
        state: BankingGraphState,
    ) -> dict[str, GraphExecutionStatus]:
        intent = validate_graph_state(state)
        domain_results = merge_domain_results(
            {},
            state.get("domain_results", {}),
        )
        if list(domain_results) != intent.domains:
            raise GraphStateValidationError(
                "Graph results do not match the requested domains"
            )
        failed_count = sum(
            result["status"] == "failed"
            for result in domain_results.values()
        )
        if failed_count == len(domain_results):
            status: GraphExecutionStatus = "failed"
        elif failed_count:
            status = "partial_failure"
        else:
            status = "succeeded"
        return {"execution_status": status}

    def mark_not_dispatched(
        _state: BankingGraphState,
    ) -> dict[str, GraphExecutionStatus]:
        return {"execution_status": "not_dispatched"}

    def mark_completed(
        _state: BankingGraphState,
    ) -> dict[str, str]:
        return {"graph_status": "completed"}

    graph.add_node("initialize", initialize)
    graph.add_node("execute_domain", execute_domain)
    graph.add_node("aggregate_results", aggregate_results)
    graph.add_node("not_dispatched", mark_not_dispatched)
    graph.add_node("complete", mark_completed)
    graph.add_edge(START, "initialize")
    graph.add_conditional_edges(
        "initialize",
        route_intent,
        {"not_dispatched": "not_dispatched"},
    )
    graph.add_edge("execute_domain", "aggregate_results")
    graph.add_edge("aggregate_results", "complete")
    graph.add_edge("not_dispatched", "complete")
    graph.add_edge("complete", END)
    return graph.compile()


def _domain_execution_state(
    result: DomainExecutionResult,
) -> DomainExecutionState:
    if result.status == "failed":
        if result.data is not None or result.failure_code != "operation_failed":
            raise GraphStateValidationError(
                "Failed domain result is not safely shaped"
            )
        return {
            "status": "failed",
            "data": None,
            "failure_code": "operation_failed",
        }
    if result.data is None or result.failure_code is not None:
        raise GraphStateValidationError(
            "Successful domain result is not safely shaped"
        )
    return {"status": result.status, "data": result.data}
