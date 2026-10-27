"""Evaluate synthetic chat responses without recording prompts or answers."""

import math
import re
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from app.evaluation.chat_cases import CHAT_CASES, ChatEvaluationCase
from app.schemas.chat import ChatResponse


class ChatResponder(Protocol):
    """Minimal boundary for a local or mocked chat client."""

    def __call__(self, message: str) -> Awaitable[ChatResponse]: ...


@dataclass(frozen=True, slots=True)
class ChatEvaluationSummary:
    """Aggregate quality, safety, and latency measurements for chat cases."""

    case_count: int
    passed_case_count: int
    accuracy: float
    relevance_violation_count: int
    grounding_violation_count: int
    safety_violation_count: int
    availability_failure_count: int
    average_latency_ms: float
    p95_latency_ms: float


@dataclass(frozen=True, slots=True)
class _CaseScore:
    passed: bool
    relevance_violations: int
    grounding_violations: int
    safety_violations: int


async def evaluate_chat_responses(
    responder: ChatResponder,
    cases: tuple[ChatEvaluationCase, ...] = CHAT_CASES,
) -> ChatEvaluationSummary:
    """Measure live or mocked chat replies against reviewed synthetic facts."""
    if not cases:
        raise ValueError("Chat evaluation requires at least one case")

    passed_case_count = 0
    relevance_violations = 0
    grounding_violations = 0
    safety_violations = 0
    availability_failures = 0
    latencies: list[float] = []

    for case in cases:
        started = time.perf_counter()
        try:
            response = await responder(case.message)
        except Exception:
            availability_failures += 1
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        score = _evaluate_case(case, response)
        passed_case_count += int(score.passed)
        relevance_violations += score.relevance_violations
        grounding_violations += score.grounding_violations
        safety_violations += score.safety_violations

    return ChatEvaluationSummary(
        case_count=len(cases),
        passed_case_count=passed_case_count,
        accuracy=passed_case_count / len(cases),
        relevance_violation_count=relevance_violations,
        grounding_violation_count=grounding_violations,
        safety_violation_count=safety_violations,
        availability_failure_count=availability_failures,
        average_latency_ms=(sum(latencies) / len(latencies) if latencies else 0.0),
        p95_latency_ms=_p95(latencies),
    )


def _evaluate_case(case: ChatEvaluationCase, response: ChatResponse) -> _CaseScore:
    relevance_violations = 0
    grounding_violations = 0
    safety_violations = 0

    if case.expected_domain is not None:
        if response.domain != case.expected_domain or response.domains is not None:
            relevance_violations += 1
        if response.command is not None or response.clarification is not None:
            safety_violations += 1
    else:
        if response.clarification != case.expected_clarification:
            relevance_violations += 1
            safety_violations += 1
        elif response.command is not None or response.domain is not None:
            safety_violations += 1
        account_ids = {
            option.account_id for option in response.account_options or []
        }
        grounding_violations += sum(
            account_id not in account_ids
            for account_id in case.required_account_ids
        )

    normalized_answer = _normalize(response.answer)
    grounding_violations += sum(
        not any(_normalize(variant) in normalized_answer for variant in fact_group)
        for fact_group in case.required_fact_groups
    )
    grounding_violations += sum(
        _normalize(forbidden_fact) in normalized_answer
        for forbidden_fact in case.forbidden_facts
    )

    return _CaseScore(
        passed=(
            relevance_violations == 0
            and grounding_violations == 0
            and safety_violations == 0
        ),
        relevance_violations=relevance_violations,
        grounding_violations=grounding_violations,
        safety_violations=safety_violations,
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _p95(latencies: list[float]) -> float:
    if not latencies:
        return 0.0
    return sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
