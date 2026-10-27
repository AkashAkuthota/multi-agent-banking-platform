"""Trusted in-process customer identity for MCP tool execution."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.core.auth import get_current_customer


@dataclass(frozen=True, slots=True)
class AuthenticatedExecutionContext:
    """Immutable customer identity established by trusted application code."""

    customer_id: int

    def __post_init__(self) -> None:
        if type(self.customer_id) is not int or self.customer_id <= 0:
            raise ValueError("customer_id must be a positive integer")


_authenticated_execution_context: ContextVar[
    AuthenticatedExecutionContext | None
] = ContextVar(
    "mcp_authenticated_execution_context",
    default=None,
)


def get_authenticated_execution_context(
    customer_id: Annotated[int, Depends(get_current_customer)],
) -> AuthenticatedExecutionContext:
    """Create an MCP execution context only from a JWT-verified customer."""
    return AuthenticatedExecutionContext(customer_id)


@contextmanager
def authenticated_customer_context(
    execution_context: AuthenticatedExecutionContext,
) -> Generator[None, None, None]:
    """Bind a trusted execution context for one MCP invocation."""
    if not isinstance(execution_context, AuthenticatedExecutionContext):
        raise TypeError("execution_context must be authenticated")

    token: Token[AuthenticatedExecutionContext | None] = (
        _authenticated_execution_context.set(execution_context)
    )
    try:
        yield
    finally:
        _authenticated_execution_context.reset(token)


def get_authenticated_customer_id() -> int:
    """Return the bound customer ID or fail closed when invoked without context."""
    execution_context = _authenticated_execution_context.get()
    if execution_context is None:
        raise PermissionError("Authenticated customer context is required")
    return execution_context.customer_id
