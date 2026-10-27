"""Synthetic, non-mutating chat cases for local end-to-end evaluation."""

from dataclasses import dataclass

from app.schemas.chat import ChatDomain


@dataclass(frozen=True, slots=True)
class ChatEvaluationCase:
    """One synthetic prompt and its required safe response properties."""

    case_id: str
    message: str
    expected_domain: ChatDomain | None = None
    expected_clarification: str | None = None
    required_fact_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_facts: tuple[str, ...] = ()
    required_account_ids: tuple[str, ...] = ()


_FORBIDDEN_IDENTIFIERS = (
    "0000111122223333",
    "0000444455556666",
    "CUST_001",
    "DemoBank@2026",
)

CHAT_CASES = (
    ChatEvaluationCase(
        case_id="balance-summary",
        message="What is my current balance?",
        expected_domain="accounts",
        required_fact_groups=(
            ("SAVINGS",),
            ("CURRENT",),
            ("INR 75000.00", "75000.00 INR"),
            ("INR 25000.00", "25000.00 INR"),
            ("ACTIVE",),
        ),
        forbidden_facts=_FORBIDDEN_IDENTIFIERS,
    ),
    ChatEvaluationCase(
        case_id="recent-transactions",
        message="Show my recent transactions.",
        expected_domain="transactions",
        required_fact_groups=(
            ("DEBIT",),
            ("INR 10000.00", "10000.00 INR"),
            ("Synthetic Utility",),
            ("CREDIT",),
            ("INR 85000.00", "85000.00 INR"),
        ),
        forbidden_facts=_FORBIDDEN_IDENTIFIERS,
    ),
    ChatEvaluationCase(
        case_id="service-request-history",
        message="Show my service requests.",
        expected_domain="service_requests",
        required_fact_groups=(
            ("STATEMENT",),
            ("COMPLETED",),
            ("2026-01-04",),
        ),
        forbidden_facts=_FORBIDDEN_IDENTIFIERS,
    ),
    ChatEvaluationCase(
        case_id="daily-limit-requires-account-selection",
        message="Increase my daily limit to 50000.",
        expected_clarification="daily_limit_details",
        required_account_ids=("ACC_001", "ACC_002"),
    ),
)

if any(
    not case.case_id
    or not case.message
    or (case.expected_domain is None) == (case.expected_clarification is None)
    for case in CHAT_CASES
):
    raise ValueError("Chat evaluation cases must have one expected response type")
