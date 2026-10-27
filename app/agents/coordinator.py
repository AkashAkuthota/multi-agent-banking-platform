"""Deterministic routing to banking-domain agents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy.exc import SQLAlchemyError

from app.agents.accounts_agent import AccountsAgent
from app.agents.service_agent import ServiceAgent
from app.agents.transactions_agent import TransactionsAgent
from app.mcp.context import AuthenticatedExecutionContext

AgentDomain = Literal["accounts", "transactions", "service_requests"]
IntentOutcome = Literal["supported", "unknown", "ambiguous", "unsupported"]
DomainExecutionStatus = Literal["succeeded", "empty", "failed"]
AggregateExecutionStatus = Literal[
    "not_dispatched",
    "succeeded",
    "partial_failure",
    "failed",
]
_DOMAIN_ORDER: tuple[AgentDomain, ...] = (
    "accounts",
    "transactions",
    "service_requests",
)

_OPERATIONAL_ERRORS = (PermissionError, RuntimeError, SQLAlchemyError)


class DomainAgent(Protocol):
    async def execute(
        self,
        execution_context: AuthenticatedExecutionContext,
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class DomainExecutionResult:
    """One domain's sanitized execution status and MCP structured result."""

    domain: AgentDomain
    status: DomainExecutionStatus
    data: dict[str, object] | None
    failure_code: Literal["operation_failed"] | None = None


@dataclass(frozen=True, slots=True)
class AggregateExecutionResult:
    """Execution results in deterministic domain order."""

    outcome: IntentOutcome
    status: AggregateExecutionStatus
    results: tuple[DomainExecutionResult, ...]

    @property
    def total_failure(self) -> bool:
        """Whether every selected domain failed."""
        return self.status == "failed"


class UnsupportedDomainError(ValueError):
    """Raised when a domain has no registered server-side agent."""


class Coordinator:
    """Execute selected domains through the fixed server-side agent map."""

    def __init__(
        self,
        agents: Mapping[AgentDomain, DomainAgent] | None = None,
    ) -> None:
        configured_agents = (
            agents
            if agents is not None
            else {
                "accounts": AccountsAgent(),
                "transactions": TransactionsAgent(),
                "service_requests": ServiceAgent(),
            }
        )
        invalid_domains = set(configured_agents).difference(_DOMAIN_ORDER)
        if invalid_domains:
            raise UnsupportedDomainError("Agent map contains unsupported domains")
        self._agents: dict[AgentDomain, DomainAgent] = dict(configured_agents)

    def validate_domains(self, domains: Sequence[AgentDomain]) -> None:
        """Fail before dispatch if any requested domain has no registered agent."""
        for domain in domains:
            self._get_agent(domain)

    async def execute_domain_result(
        self,
        domain: AgentDomain,
        execution_context: AuthenticatedExecutionContext,
    ) -> DomainExecutionResult:
        """Execute one selected domain and return a sanitized outcome."""
        agent = self._get_agent(domain)
        return await self._execute_domain(domain, agent, execution_context)

    @staticmethod
    async def _execute_domain(
        domain: AgentDomain,
        agent: DomainAgent,
        execution_context: AuthenticatedExecutionContext,
    ) -> DomainExecutionResult:
        try:
            data = await agent.execute(execution_context)
        except _OPERATIONAL_ERRORS:
            return DomainExecutionResult(
                domain=domain,
                status="failed",
                data=None,
                failure_code="operation_failed",
            )

        is_empty = data.get("result") == []
        return DomainExecutionResult(
            domain=domain,
            status="empty" if is_empty else "succeeded",
            data=data,
        )

    def _get_agent(self, domain: AgentDomain) -> DomainAgent:
        if domain not in _DOMAIN_ORDER:
            raise UnsupportedDomainError("Unsupported banking domain")
        try:
            return self._agents[domain]
        except KeyError:
            raise UnsupportedDomainError(
                "No agent is registered for a requested domain"
            ) from None
