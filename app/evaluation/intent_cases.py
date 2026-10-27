"""Synthetic intent-classification cases for repeatable LLM evaluation."""

from dataclasses import dataclass

from app.llm.schemas import CommandAction, Intent, IntentDomain


@dataclass(frozen=True, slots=True)
class IntentEvaluationCase:
    case_id: str
    message: str
    expected: Intent


INTENT_CASES = (
    IntentEvaluationCase(
        "account-balance",
        "What is my account balance?",
        Intent(outcome="supported", domains=["accounts"]),
    ),
    IntentEvaluationCase(
        "current-account-balance",
        "What is my current balance?",
        Intent(outcome="supported", domains=["accounts"]),
    ),
    IntentEvaluationCase(
        "account-details",
        "List my accounts.",
        Intent(outcome="supported", domains=["accounts"]),
    ),
    IntentEvaluationCase(
        "transaction-history",
        "Show my recent transactions.",
        Intent(outcome="supported", domains=["transactions"]),
    ),
    IntentEvaluationCase(
        "spending-history",
        "How much did I spend recently?",
        Intent(outcome="supported", domains=["transactions"]),
    ),
    IntentEvaluationCase(
        "service-request-status",
        "What is the status of my service requests?",
        Intent(outcome="supported", domains=["service_requests"]),
    ),
    IntentEvaluationCase(
        "service-request-details",
        "List my existing service requests.",
        Intent(outcome="supported", domains=["service_requests"]),
    ),
    IntentEvaluationCase(
        "multi-domain",
        "Show my balance and recent transactions.",
        Intent(outcome="supported", domains=["accounts", "transactions"]),
    ),
    IntentEvaluationCase(
        "background-domain-mention",
        "I made a transaction yesterday. What is my account balance?",
        Intent(outcome="supported", domains=["accounts"]),
    ),
    IntentEvaluationCase(
        "ambiguous-banking-request",
        "Do something with my account.",
        Intent(outcome="ambiguous", domains=[]),
    ),
    IntentEvaluationCase(
        "unrelated-request",
        "What will the weather be tomorrow?",
        Intent(outcome="unknown", domains=[]),
    ),
    IntentEvaluationCase(
        "incomplete-transfer-details",
        "Transfer money to another account.",
        Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(kind="internal_transfer"),
        ),
    ),
    IntentEvaluationCase(
        "daily-limit-proposal",
        "Increase the daily limit on ACC_001 to 20000 INR.",
        Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="daily_limit_change",
                account_id="ACC_001",
                requested_limit="20000",
            ),
        ),
    ),
    IntentEvaluationCase(
        "daily-limit-missing-account",
        "Increase my current daily limit to 20000.",
        Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="daily_limit_change",
                requested_limit="20000",
            ),
        ),
    ),
    IntentEvaluationCase(
        "internal-transfer-proposal",
        "Transfer 100 INR from ACC_001 to ACC_DEMO_002.",
        Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="internal_transfer",
                source_account_id="ACC_001",
                destination_account_id="ACC_DEMO_002",
                amount="100",
            ),
        ),
    ),
    IntentEvaluationCase(
        "service-request-proposal",
        "Request a replacement card.",
        Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="service_request",
                request_type="CARD_REPLACEMENT",
            ),
        ),
    ),
    IntentEvaluationCase(
        "explicit-proposal-confirmation",
        "Confirm proposal 66b74620-aefb-42ae-9c94-796a2120991c.",
        Intent(
            outcome="supported",
            domains=[],
            action=CommandAction(
                kind="confirm",
                request_id="66b74620-aefb-42ae-9c94-796a2120991c",
            ),
        ),
    ),
    IntentEvaluationCase(
        "unsupported-external-transfer",
        "Transfer money to another person's account.",
        Intent(outcome="unsupported", domains=[]),
    ),
    IntentEvaluationCase(
        "mixed-supported-and-unsupported",
        "Show my balance and transfer money to another account.",
        Intent(outcome="unsupported", domains=[]),
    ),
)

_SUPPORTED_DOMAINS: tuple[IntentDomain, ...] = (
    "accounts",
    "transactions",
    "service_requests",
)
if any(
    not case.case_id
    or not case.message
    or any(domain not in _SUPPORTED_DOMAINS for domain in case.expected.domains)
    for case in INTENT_CASES
):
    raise ValueError("Intent evaluation cases must be valid and self-contained")
