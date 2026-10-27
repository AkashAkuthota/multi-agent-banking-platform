"""Authenticated banking chat endpoint."""

import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.agents.coordinator import AggregateExecutionResult, Coordinator
from app.chat.followups import accounts_from_tool_result, apply_session_context
from app.core.database import async_session
from app.core.session_crypto import SessionEncryptionError
from app.graph import aggregate_execution_result, build_graph
from app.llm.client import IntentClassificationError, create_intent_provider
from app.llm.schemas import CommandAction, Intent
from app.llm.presentation import (
    build_aggregate_presentation_data,
    build_presentation_data,
)
from app.llm.response_generator import (
    ResponseGenerationError,
    ResponseGenerator,
)
from app.llm.router import IntentRouter
from app.mcp.context import (
    AuthenticatedExecutionContext,
    get_authenticated_execution_context,
)
from app.mcp.server import call_authenticated_mcp_tool
from app.policies import PolicyViolationError
from app.schemas.chat import (
    ChatAccountOption,
    ChatCommandResponse,
    ChatPendingProposal,
    ChatRequest,
    ChatResponse,
)
from app.services.chat_session import (
    ChatSessionState,
    ChatSessionStore,
    EncryptedChatSessionStore,
    SessionAccount,
)
from app.services.banking_command_service import decide_customer_proposal
from app.services.daily_limit_service import (
    DailyLimitConfigurationError,
    DailyLimitConflict,
    DailyLimitNotFound,
)

router = APIRouter(tags=["Chat"])
logger = logging.getLogger("app.chat")
_PROPOSAL_ID_IN_MESSAGE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def get_intent_router() -> IntentRouter:
    """Build the internal classifier, which receives no execution context."""
    return IntentRouter(intent_provider=create_intent_provider())


def get_coordinator() -> Coordinator:
    """Build the deterministic domain dispatcher."""
    return Coordinator()


def get_response_generator() -> ResponseGenerator:
    """Build the wording layer, isolated from identity and MCP execution."""
    return ResponseGenerator()


def get_chat_session_store() -> ChatSessionStore:
    """Load the customer-scoped encrypted session store."""
    return EncryptedChatSessionStore()


@router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def chat(
    chat_request: ChatRequest,
    request: Request,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
    intent_router: Annotated[IntentRouter, Depends(get_intent_router)],
    coordinator: Annotated[Coordinator, Depends(get_coordinator)],
    response_generator: Annotated[
        ResponseGenerator,
        Depends(get_response_generator),
    ],
    session_store: Annotated[ChatSessionStore, Depends(get_chat_session_store)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            description=(
                "Required for command proposals. Generate once per logical user "
                "turn and reuse on retries."
            ),
        ),
    ] = None,
) -> ChatResponse:
    """Classify and execute an authenticated read or sandbox command request."""
    stage = "classification"
    classification_ms = 0.0
    graph_ms = 0.0
    response_ms = 0.0
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Chat requests do not accept query parameters",
        )

    try:
        session_state = await session_store.load(execution_context.customer_id)
        started = time.perf_counter()
        intent = await intent_router.classify(chat_request.message)
        intent = apply_session_context(
            intent,
            chat_request.message,
            session_state,
        )
        classification_ms = (time.perf_counter() - started) * 1000
        aggregate: AggregateExecutionResult | None = None
        if intent.action is not None:
            stage = "command_execution"
            chat_response = await _execute_chat_command(
                intent,
                execution_context,
                idempotency_key,
                chat_request.message,
            )
            logger.info(
                "chat.command_completed request_id=%s command_action=%s",
                getattr(request.state, "request_id", "unavailable"),
                intent.action.kind,
            )
        elif intent.outcome != "supported":
            logger.info(
                "chat.not_dispatched request_id=%s outcome=%s classification_ms=%.2f",
                getattr(request.state, "request_id", "unavailable"),
                intent.outcome,
                classification_ms,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Request does not identify a supported banking domain",
            )
        else:
            stage = "domain_execution"
            started = time.perf_counter()
            graph = build_graph(coordinator, execution_context)
            graph_state = await graph.ainvoke({"intent": intent})
            aggregate = aggregate_execution_result(graph_state)
            graph_ms = (time.perf_counter() - started) * 1000
            if (
                len(intent.domains) == 1
                and len(aggregate.results) == 1
                and aggregate.results[0].status != "failed"
                and aggregate.results[0].data is not None
            ):
                result = aggregate.results[0]
                presentation_data = build_presentation_data(
                    result.domain,
                    result.data,
                )
            else:
                presentation_data = build_aggregate_presentation_data(aggregate)
            stage = "response_generation"
            started = time.perf_counter()
            answer = await response_generator.generate(presentation_data)
            response_ms = (time.perf_counter() - started) * 1000
            chat_response = ChatResponse(
                domain=intent.domains[0] if len(intent.domains) == 1 else None,
                domains=intent.domains if len(intent.domains) > 1 else None,
                answer=answer,
            )
        _remember_turn(session_state, intent, chat_response, aggregate)
        await session_store.save(execution_context.customer_id, session_state)
        chat_response = _with_pending_proposal(chat_response, session_state)
    except SessionEncryptionError as error:
        _log_chat_failure(request, "session", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat session is unavailable",
        ) from None
    except IntentClassificationError as error:
        _log_chat_failure(request, "classification", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Intent classification is unavailable",
        ) from None
    except ResponseGenerationError as error:
        _log_chat_failure(request, "response_generation", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Response generation is unavailable",
        ) from None
    except (
        DailyLimitConfigurationError,
        DailyLimitConflict,
        DailyLimitNotFound,
        PolicyViolationError,
    ) as error:
        _log_chat_failure(request, stage, error)
        _raise_command_http_error(error)
    except HTTPException as error:
        _log_chat_failure(request, stage, error)
        if error.status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        }:
            raise HTTPException(
                status_code=error.status_code,
                detail="Banking request could not be completed",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Banking request could not be completed",
        ) from None
    except Exception as error:
        _log_chat_failure(request, stage, error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Banking request could not be completed",
        ) from None

    if aggregate is not None:
        logger.info(
            "chat.completed request_id=%s domains=%s execution_status=%s "
            "classification_ms=%.2f graph_ms=%.2f response_ms=%.2f",
            getattr(request.state, "request_id", "unavailable"),
            ",".join(intent.domains),
            aggregate.status,
            classification_ms,
            graph_ms,
            response_ms,
        )
    return chat_response


@router.post(
    "/chat/pending-proposal/{decision}",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def decide_pending_chat_proposal(
    decision: Literal["confirm", "cancel"],
    request: Request,
    execution_context: Annotated[
        AuthenticatedExecutionContext,
        Depends(get_authenticated_execution_context),
    ],
    session_store: Annotated[ChatSessionStore, Depends(get_chat_session_store)],
) -> ChatResponse:
    """Confirm or cancel the session's pending proposal without reading chat text."""
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Chat requests do not accept query parameters",
        )
    try:
        session_state = await session_store.load(execution_context.customer_id)
        proposal_id = session_state.active_pending_proposal_id()
        if proposal_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No pending proposal in this session",
            )
        async with async_session() as session:
            result = await decide_customer_proposal(
                session,
                execution_context.customer_id,
                proposal_id,
                decision,
            )
        response = _decision_response(result, decision)
        _remember_turn(session_state, None, response, None)
        await session_store.save(execution_context.customer_id, session_state)
        logger.info(
            "chat.pending_proposal_decided request_id=%s decision=%s",
            getattr(request.state, "request_id", "unavailable"),
            decision,
        )
        return _with_pending_proposal(response, session_state)
    except SessionEncryptionError as error:
        _log_chat_failure(request, "session", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat session is unavailable",
        ) from None
    except (
        DailyLimitConfigurationError,
        DailyLimitConflict,
        DailyLimitNotFound,
        PolicyViolationError,
    ) as error:
        if isinstance(error, DailyLimitNotFound):
            try:
                session_state = await session_store.load(execution_context.customer_id)
                session_state.clear_pending_proposal()
                await session_store.save(execution_context.customer_id, session_state)
            except SessionEncryptionError:
                pass
        _log_chat_failure(request, "command_execution", error)
        _raise_command_http_error(error)
    except HTTPException:
        raise
    except Exception as error:
        _log_chat_failure(request, "command_execution", error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Banking request could not be completed",
        ) from None


async def _execute_chat_command(
    intent: Intent,
    execution_context: AuthenticatedExecutionContext,
    idempotency_key: str | None,
    user_message: str,
) -> ChatResponse:
    action = intent.action
    if action is None:
        raise RuntimeError("Command dispatcher received an intent without an action")
    if action.kind == "daily_limit_change":
        if action.account_id is None or action.requested_limit is None:
            return await _daily_limit_clarification(action, execution_context)

        result = await call_authenticated_mcp_tool(
            execution_context,
            "propose_daily_limit_change",
            {
                "account_id": action.account_id,
                "requested_limit": _parse_command_amount(action.requested_limit),
                "idempotency_key": _require_idempotency_key(idempotency_key),
            },
        )
        return _proposal_response(
            result,
            kind="daily_limit_change",
            summary=(
                f"Daily-limit change for {action.account_id} "
                f"to {action.requested_limit} was proposed."
            ),
        )

    if action.kind == "internal_transfer":
        if (
            action.source_account_id is None
            or action.destination_account_id is None
            or action.amount is None
        ):
            return ChatResponse(
                answer=(
                    "Please provide the source account ID, destination account ID, "
                    "and transfer amount. I will prepare a proposal for your review."
                ),
                clarification="transfer_details",
            )
        result = await call_authenticated_mcp_tool(
            execution_context,
            "propose_internal_transfer",
            {
                "source_account_id": action.source_account_id,
                "destination_account_id": action.destination_account_id,
                "amount": _parse_command_amount(action.amount),
                "idempotency_key": _require_idempotency_key(idempotency_key),
            },
        )
        return _proposal_response(
            result,
            kind="internal_transfer",
            summary=(
                f"Transfer of {action.amount} from {action.source_account_id} "
                f"to {action.destination_account_id} was proposed."
            ),
        )

    if action.kind == "service_request":
        if action.request_type is None:
            return ChatResponse(
                answer=(
                    "Please specify a service request: statement, cheque book, "
                    "or card replacement."
                ),
                clarification="service_request_type",
            )
        result = await call_authenticated_mcp_tool(
            execution_context,
            "create_service_request",
            {
                "request_type": action.request_type,
                "idempotency_key": _require_idempotency_key(idempotency_key),
            },
        )
        return _proposal_response(
            result,
            kind="service_request",
            summary=f"A {action.request_type} service request was proposed.",
        )

    if action.kind in {"confirm", "cancel"}:
        message_proposal_id = _proposal_id_from_message(user_message)
        if message_proposal_id is None:
            return ChatResponse(
                answer=(
                    "A reply of yes is not enough to apply a change. Use Confirm "
                    "or Cancel on the pending proposal in this session, or include "
                    "the full proposal ID in your message."
                ),
                clarification="proposal_id",
            )
        if action.request_id is not None:
            classified_id = _parse_proposal_id(action.request_id)
            if classified_id != message_proposal_id:
                raise PolicyViolationError(
                    "Proposal ID in the message does not match the classified ID"
                )
        async with async_session() as session:
            result = await decide_customer_proposal(
                session,
                execution_context.customer_id,
                message_proposal_id,
                action.kind,
            )
        return _decision_response(result, action.kind)

    raise RuntimeError("Classifier returned an unsupported command action")


async def _daily_limit_clarification(
    action: CommandAction,
    execution_context: AuthenticatedExecutionContext,
) -> ChatResponse:
    """Request missing daily-limit fields without selecting an account implicitly."""
    account_options: list[ChatAccountOption] | None = None
    missing_fields: list[str] = []
    if action.account_id is None:
        account_result = await call_authenticated_mcp_tool(
            execution_context,
            "get_my_accounts",
            {},
        )
        account_options = _daily_limit_account_options(account_result)
        missing_fields.append("account ID")
    if action.requested_limit is None:
        missing_fields.append("new daily limit amount")

    if action.account_id is None and account_options:
        answer = (
            "Please choose the account for this daily-limit change from the "
            "available account options"
        )
    elif action.account_id is None:
        answer = "Please provide the account ID for this daily-limit change"
    else:
        answer = "Please provide the new daily limit amount"
    if action.requested_limit is None and action.account_id is None:
        answer += " and provide the new daily limit amount"
    answer += ". I will prepare a proposal for your review."

    if not missing_fields:
        raise RuntimeError("Daily-limit clarification has no missing fields")
    return ChatResponse(
        answer=answer,
        clarification="daily_limit_details",
        account_options=account_options,
    )


def _daily_limit_account_options(
    tool_result: dict[str, object],
) -> list[ChatAccountOption]:
    rows = tool_result.get("result")
    if not isinstance(rows, list):
        raise RuntimeError("Account lookup returned an invalid result")
    try:
        options: list[ChatAccountOption] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("status"), str):
                raise TypeError("Account lookup row is invalid")
            if row["status"] == "ACTIVE":
                options.append(
                    ChatAccountOption.model_validate(
                        {
                            "account_id": row["account_id"],
                            "account_type": row["account_type"],
                        }
                    )
                )
        return sorted(options, key=lambda option: option.account_id)
    except (KeyError, ValueError, TypeError) as error:
        raise RuntimeError("Account lookup returned an invalid result") from error


def _proposal_response(
    result: dict[str, object],
    *,
    kind: Literal["daily_limit_change", "internal_transfer", "service_request"],
    summary: str,
) -> ChatResponse:
    command = _command_response(result, kind=kind, action="proposal")
    if command.status == "CONFIRMED" and command.applied:
        answer = (
            f"{summary} Proposal {command.request_id} was already confirmed and "
            "applied; this retry made no additional change."
        )
    elif command.status == "CANCELLED":
        answer = (
            f"{summary} Proposal {command.request_id} was already cancelled; "
            "no change was applied."
        )
    elif command.status == "PENDING" and not command.applied:
        answer = (
            f"{summary} No change has been applied. Review proposal "
            f"{command.request_id}. Use Confirm or Cancel in this session, or "
            f"reply 'confirm {command.request_id}' / 'cancel {command.request_id}'. "
            "A reply of yes will not apply the change."
        )
    else:
        answer = (
            f"{summary} Proposal {command.request_id} is {command.status.lower()}; "
            "no new change was applied."
        )
    return ChatResponse(
        command=command,
        answer=answer,
    )


def _decision_response(
    result: dict[str, str | bool | None],
    decision: Literal["confirm", "cancel"],
) -> ChatResponse:
    kind = _command_kind_from_result(result)
    command = _command_response(result, kind=kind, action=decision)
    if decision == "confirm" and command.status == "CONFIRMED":
        answer = f"Proposal {command.request_id} is confirmed and applied."
    elif decision == "cancel" and command.status == "CANCELLED":
        answer = f"Proposal {command.request_id} is cancelled; no change was applied."
    else:
        answer = (
            f"Proposal {command.request_id} is {command.status.lower()}; "
            "no new change was applied."
        )
    return ChatResponse(command=command, answer=answer)


def _command_kind_from_result(
    result: dict[str, str | bool | None],
) -> Literal["daily_limit_change", "internal_transfer", "service_request"]:
    if "requested_limit" in result:
        return "daily_limit_change"
    if "source_account_id" in result:
        return "internal_transfer"
    if "request_type" in result:
        return "service_request"
    raise RuntimeError("Proposal result does not identify its command type")


def _command_response(
    result: dict[str, object] | dict[str, str | bool | None],
    *,
    kind: Literal["daily_limit_change", "internal_transfer", "service_request"],
    action: Literal["proposal", "confirm", "cancel"],
) -> ChatCommandResponse:
    request_id = result.get("request_id")
    status_value = result.get("status")
    if not isinstance(request_id, str) or not isinstance(status_value, str):
        raise RuntimeError("Proposal result is missing its state or identifier")
    try:
        return ChatCommandResponse(
            kind=kind,
            action=action,
            request_id=UUID(request_id),
            status=status_value,
            applied=result.get("applied") is True,
            expires_at=(
                result.get("expires_at")
                if isinstance(result.get("expires_at"), str)
                else None
            ),
        )
    except ValueError as error:
        raise RuntimeError("Proposal result has an invalid state or identifier") from error


def _parse_command_amount(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise PolicyViolationError("Command amount is invalid") from error


def _require_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise PolicyViolationError(
            "Send an Idempotency-Key header when proposing a banking command"
        )
    normalized = value.strip()
    if len(normalized) > 128:
        raise PolicyViolationError("Idempotency-Key must not exceed 128 characters")
    return normalized


def _parse_proposal_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise PolicyViolationError("Proposal ID is invalid") from error


def _proposal_id_from_message(message: str) -> UUID | None:
    matches = _PROPOSAL_ID_IN_MESSAGE.findall(message)
    if len(matches) != 1:
        return None
    try:
        return UUID(matches[0])
    except ValueError:
        return None


def _remember_turn(
    state: ChatSessionState,
    intent: Intent | None,
    response: ChatResponse,
    aggregate: AggregateExecutionResult | None,
) -> None:
    """Update application session state. Never stores raw chat text."""
    action = intent.action if intent is not None else None
    if aggregate is not None:
        accounts = _accounts_from_aggregate(aggregate)
        if accounts:
            state.accounts = accounts
    if response.account_options:
        state.accounts = [
            SessionAccount(
                account_id=option.account_id,
                account_type=option.account_type,
            )
            for option in response.account_options
        ]
    if action is not None:
        if action.account_id is not None:
            state.last_referenced_account_id = action.account_id
        elif action.source_account_id is not None:
            state.last_referenced_account_id = action.source_account_id
        if action.requested_limit is not None:
            state.last_requested_limit = action.requested_limit
    if response.clarification is not None:
        state.last_clarification = response.clarification
        return
    state.last_clarification = None
    command = response.command
    if command is None:
        return
    if command.status == "PENDING" and not command.applied:
        state.pending_proposal_id = command.request_id
        state.pending_proposal_kind = command.kind
        state.pending_proposal_expires_at = command.expires_at
        return
    if state.pending_proposal_id == command.request_id:
        state.clear_pending_proposal()


def _accounts_from_aggregate(
    aggregate: AggregateExecutionResult,
) -> list[SessionAccount]:
    for result in aggregate.results:
        if result.domain == "accounts" and result.data is not None:
            return accounts_from_tool_result(result.data)
    return []


def _with_pending_proposal(
    response: ChatResponse,
    state: ChatSessionState,
) -> ChatResponse:
    pending_id = state.active_pending_proposal_id()
    pending = None
    if pending_id is not None and state.pending_proposal_kind is not None:
        pending = ChatPendingProposal(
            request_id=pending_id,
            kind=state.pending_proposal_kind,
            expires_at=state.pending_proposal_expires_at,
        )
    return response.model_copy(update={"pending_proposal": pending})


def _raise_command_http_error(error: Exception) -> NoReturn:
    if isinstance(error, DailyLimitNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Banking resource or proposal not found",
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
            detail="Banking command configuration is unavailable",
        ) from error
    raise error


def _log_chat_failure(request: Request, stage: str, error: Exception) -> None:
    logger.warning(
        "chat.failed request_id=%s stage=%s error_type=%s",
        getattr(request.state, "request_id", "unavailable"),
        stage,
        type(error).__name__,
    )
