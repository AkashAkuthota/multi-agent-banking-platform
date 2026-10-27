"""Internal language-model interfaces for banking request classification."""

from app.llm.client import (
    IntentClassificationError,
    GeminiStructuredOutputClient,
    GeminiStructuredOutputError,
    OllamaFallbackStructuredOutputClient,
    OpenAIIntentProvider,
    OpenAIResponsesClient,
    OpenAIResponsesError,
    OllamaStructuredOutputClient,
    OllamaStructuredOutputError,
    StructuredIntentProvider,
    StructuredOutputError,
    create_intent_provider,
    create_structured_output_client,
)
from app.llm.presentation import (
    AccountPresentation,
    AccountsPresentationData,
    PresentationDataError,
    ServiceRequestPresentation,
    ServiceRequestsPresentationData,
    TransactionPresentation,
    TransactionsPresentationData,
    build_presentation_data,
)
from app.llm.response_generator import (
    GeneratedAnswer,
    ResponseGenerationError,
    ResponseGenerator,
)
from app.llm.router import IntentRouter
from app.llm.schemas import Intent, IntentDomain

__all__ = [
    "AccountPresentation",
    "AccountsPresentationData",
    "GeneratedAnswer",
    "GeminiStructuredOutputClient",
    "GeminiStructuredOutputError",
    "Intent",
    "IntentClassificationError",
    "IntentDomain",
    "IntentRouter",
    "OllamaFallbackStructuredOutputClient",
    "OpenAIIntentProvider",
    "OpenAIResponsesClient",
    "OpenAIResponsesError",
    "OllamaStructuredOutputClient",
    "OllamaStructuredOutputError",
    "PresentationDataError",
    "ResponseGenerationError",
    "ResponseGenerator",
    "StructuredIntentProvider",
    "StructuredOutputError",
    "ServiceRequestPresentation",
    "ServiceRequestsPresentationData",
    "TransactionPresentation",
    "TransactionsPresentationData",
    "build_presentation_data",
    "create_intent_provider",
    "create_structured_output_client",
]
