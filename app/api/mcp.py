"""Authenticated HTTP bridge to banking MCP tools."""

from decimal import Decimal
from json import JSONDecodeError
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.mcp.context import (
    AuthenticatedExecutionContext,
    get_authenticated_execution_context,
)
from app.mcp.server import MCPToolName, call_authenticated_mcp_tool
from app.services.banking_command_service import (
    cancel_internal_transfer,
    cancel_service_request_proposal,
    confirm_internal_transfer,
    confirm_service_request,
)
from app.policies import PolicyViolationError
from app.services.daily_limit_service import (
    DailyLimitConfigurationError,
    DailyLimitConflict,
    DailyLimitNotFound,
    cancel_daily_limit_change,
    confirm_daily_limit_change,
)

router = APIRouter(prefix="/mcp/tools", tags=["MCP"])

_READ_TOOLS = {
    "get_my_accounts",
    "get_my_transactions",
    "get_my_service_requests",
}


class _ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _GetDailyLimitArguments(_ToolArguments):
    account_id: str = Field(min_length=1, max_length=20)


class _ProposeDailyLimitArguments(_ToolArguments):
    account_id: str = Field(min_length=1, max_length=20)
    requested_limit: Decimal
    idempotency_key: str = Field(min_length=1, max_length=128)


class _ProposeTransferArguments(_ToolArguments):
    source_account_id: str = Field(min_length=1, max_length=20)
    destination_account_id: str = Field(min_length=1, max_length=20)
    amount: Decimal
    idempotency_key: str = Field(min_length=1, max_length=128)


class _CreateServiceRequestArguments(_ToolArguments):
    request_type: str = Field(min_length=1, max_length=30)
    idempotency_key: str = Field(min_length=1, max_length=128)


class _ConfirmDailyLimitArguments(_ToolArguments):
    request_id: UUID
    confirmed: Literal[True]


class _CancelDailyLimitArguments(_ToolArguments):
    request_id: UUID


_ARGUMENT_MODELS: dict[str, type[_ToolArguments]] = {
    "get_my_daily_limit": _GetDailyLimitArguments,
    "propose_daily_limit_change": _ProposeDailyLimitArguments,
    "propose_internal_transfer": _ProposeTransferArguments,
    "create_service_request": _CreateServiceRequestArguments,
}


async def _invoke_mcp_tool(
    execution_context: AuthenticatedExecutionContext,
    tool_name: MCPToolName,
    arguments: dict[str, object],
) -> dict[str, object]:
    try:
        return await call_authenticated_mcp_tool(
            execution_context,
            tool_name,
            arguments,
        )
    except (
        DailyLimitConfigurationError,
        DailyLimitConflict,
        DailyLimitNotFound,
        PolicyViolationError,
    ) as error:
        _raise_daily_limit_http_error(error)


@router.post("/get_my_accounts", operation_id="getMyAccountsMcpTool")
async def get_my_accounts_mcp_endpoint(
    request: Request,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Get accounts belonging to the authenticated customer."""
    await _reject_read_tool_arguments(request)
    return await _invoke_mcp_tool(execution_context, "get_my_accounts", {})


@router.post("/get_my_transactions", operation_id="getMyTransactionsMcpTool")
async def get_my_transactions_mcp_endpoint(
    request: Request,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Get transactions for the authenticated customer's accounts."""
    await _reject_read_tool_arguments(request)
    return await _invoke_mcp_tool(execution_context, "get_my_transactions", {})


@router.post(
    "/get_my_service_requests",
    operation_id="getMyServiceRequestsMcpTool",
)
async def get_my_service_requests_mcp_endpoint(
    request: Request,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Get service requests belonging to the authenticated customer."""
    await _reject_read_tool_arguments(request)
    return await _invoke_mcp_tool(
        execution_context,
        "get_my_service_requests",
        {},
    )


async def _reject_read_tool_arguments(request: Request) -> None:
    if await request.body() or request.query_params:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This MCP tool does not accept arguments",
        )


@router.post("/get_my_daily_limit", operation_id="getMyDailyLimitTool")
async def get_my_daily_limit_endpoint(
    arguments: _GetDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Read the limit for an account owned by the authenticated customer."""
    return await _invoke_mcp_tool(
        execution_context,
        "get_my_daily_limit",
        arguments.model_dump(),
    )


@router.post(
    "/propose_daily_limit_change",
    operation_id="proposeDailyLimitChangeTool",
)
async def propose_daily_limit_change_endpoint(
    arguments: _ProposeDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Propose, but never apply, a daily-limit change."""
    return await _invoke_mcp_tool(
        execution_context,
        "propose_daily_limit_change",
        arguments.model_dump(),
    )


@router.post(
    "/propose_internal_transfer",
    operation_id="proposeInternalTransferTool",
)
async def propose_internal_transfer_endpoint(
    arguments: _ProposeTransferArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Propose, but never apply, an own-account transfer."""
    return await _invoke_mcp_tool(
        execution_context,
        "propose_internal_transfer",
        arguments.model_dump(),
    )


@router.post(
    "/create_service_request",
    operation_id="proposeServiceRequestTool",
)
async def create_service_request_endpoint(
    arguments: _CreateServiceRequestArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Propose, but do not create, an allow-listed service request."""
    return await _invoke_mcp_tool(
        execution_context,
        "create_service_request",
        arguments.model_dump(),
    )


async def _run_command_action(
    operation: Callable[
        [AsyncSession, int],
        Awaitable[dict[str, str | bool | None]],
    ],
    execution_context: AuthenticatedExecutionContext,
) -> dict[str, str | bool | None]:
    try:
        async with async_session() as session:
            return await operation(
                session,
                execution_context.customer_id,
            )
    except (
        DailyLimitConfigurationError,
        DailyLimitConflict,
        DailyLimitNotFound,
        PolicyViolationError,
    ) as error:
        _raise_daily_limit_http_error(error)


@router.post("/confirm_internal_transfer")
async def confirm_internal_transfer_endpoint(
    arguments: _ConfirmDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, str | bool | None]:
    """Apply a transfer only through this separate authenticated confirmation."""
    return await _run_command_action(
        lambda session, customer_id: confirm_internal_transfer(
            session, customer_id, arguments.request_id, arguments.confirmed
        ),
        execution_context,
    )


@router.post("/cancel_internal_transfer")
async def cancel_internal_transfer_endpoint(
    arguments: _CancelDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, str | bool | None]:
    """Cancel an authenticated customer's pending transfer proposal."""
    return await _run_command_action(
        lambda session, customer_id: cancel_internal_transfer(
            session, customer_id, arguments.request_id
        ),
        execution_context,
    )


@router.post("/confirm_service_request")
async def confirm_service_request_endpoint(
    arguments: _ConfirmDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, str | bool | None]:
    """Create the service request only after authenticated confirmation."""
    return await _run_command_action(
        lambda session, customer_id: confirm_service_request(
            session, customer_id, arguments.request_id, arguments.confirmed
        ),
        execution_context,
    )


@router.post("/cancel_service_request")
async def cancel_service_request_endpoint(
    arguments: _CancelDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, str | bool | None]:
    """Cancel an authenticated customer's pending service-request proposal."""
    return await _run_command_action(
        lambda session, customer_id: cancel_service_request_proposal(
            session, customer_id, arguments.request_id
        ),
        execution_context,
    )


def _raise_daily_limit_http_error(error: Exception) -> NoReturn:
    if isinstance(error, DailyLimitNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    if isinstance(error, PolicyViolationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    if isinstance(error, DailyLimitConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if isinstance(error, DailyLimitConfigurationError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Daily-limit configuration is unavailable",
        ) from error
    raise error


@router.post("/confirm_daily_limit_change")
async def confirm_daily_limit_change_endpoint(
    arguments: _ConfirmDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, str | bool]:
    """Apply a proposal only from the separate authenticated confirmation action."""
    try:
        async with async_session() as session:
            return await confirm_daily_limit_change(
                session,
                execution_context.customer_id,
                arguments.request_id,
                arguments.confirmed,
            )
    except (
        DailyLimitConfigurationError,
        DailyLimitConflict,
        DailyLimitNotFound,
        PolicyViolationError,
    ) as error:
        _raise_daily_limit_http_error(error)


@router.post("/cancel_daily_limit_change")
async def cancel_daily_limit_change_endpoint(
    arguments: _CancelDailyLimitArguments,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, str | bool]:
    """Cancel a proposal from a separate authenticated customer action."""
    try:
        async with async_session() as session:
            return await cancel_daily_limit_change(
                session,
                execution_context.customer_id,
                arguments.request_id,
            )
    except (
        DailyLimitConfigurationError,
        DailyLimitConflict,
        DailyLimitNotFound,
        PolicyViolationError,
    ) as error:
        _raise_daily_limit_http_error(error)


@router.post("/{tool_name}")
async def execute_mcp_tool(
    tool_name: MCPToolName,
    request: Request,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
) -> dict[str, object]:
    """Invoke a registered MCP tool using verified identity and typed arguments."""
    body = await request.body()
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="MCP tools do not accept query parameters",
        )
    if tool_name in _READ_TOOLS:
        if body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="This MCP tool does not accept arguments",
            )
        arguments: dict[str, object] = {}
    else:
        argument_model = _ARGUMENT_MODELS[tool_name]
        if not body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="This MCP tool requires arguments",
            )
        try:
            arguments = argument_model.model_validate_json(body).model_dump()
        except (ValidationError, JSONDecodeError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid MCP tool arguments",
            ) from error

    return await _invoke_mcp_tool(
        execution_context,
        tool_name,
        arguments,
    )
