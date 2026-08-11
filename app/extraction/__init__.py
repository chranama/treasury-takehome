"""Provider-neutral label extraction boundary and deterministic test adapter."""

from app.extraction.contract import (
    ExtractionAdapter,
    ExtractionError,
    ExtractionErrorKind,
    ImageMediaType,
    PreparedImage,
)
from app.extraction.factory import ExtractionConfigurationError, create_extraction_adapter
from app.extraction.fake import FakeExtractionAdapter, FakeExtractionFailure, FakeExtractionScenario
from app.extraction.openai_adapter import (
    EXTRACTION_INSTRUCTIONS,
    PROMPT_REVISION,
    OpenAIExtractionAdapter,
    OpenAIExtractionResult,
    OpenAIUsage,
)

__all__ = [
    "ExtractionAdapter",
    "ExtractionConfigurationError",
    "ExtractionError",
    "ExtractionErrorKind",
    "FakeExtractionAdapter",
    "FakeExtractionFailure",
    "FakeExtractionScenario",
    "ImageMediaType",
    "OpenAIExtractionAdapter",
    "OpenAIExtractionResult",
    "OpenAIUsage",
    "PROMPT_REVISION",
    "EXTRACTION_INSTRUCTIONS",
    "PreparedImage",
    "create_extraction_adapter",
]
