from openai import AsyncOpenAI

from app.config import Settings
from app.extraction.contract import ExtractionAdapter
from app.extraction.fake import FakeExtractionAdapter, FakeExtractionScenario
from app.extraction.openai_adapter import OpenAIExtractionAdapter


class ExtractionConfigurationError(RuntimeError):
    """Raised when the configured extraction adapter cannot be constructed safely."""


def create_extraction_adapter(settings: Settings) -> ExtractionAdapter:
    """Construct exactly the configured adapter without an implicit fallback."""

    if settings.extraction_backend == "fake":
        try:
            scenario = FakeExtractionScenario(settings.fake_extraction_scenario)
        except ValueError as error:
            supported = ", ".join(scenario.value for scenario in FakeExtractionScenario)
            raise ExtractionConfigurationError(
                f"Unknown fake extraction scenario. Supported scenarios: {supported}."
            ) from error
        return FakeExtractionAdapter(scenario=scenario)

    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise ExtractionConfigurationError("The OpenAI extraction adapter requires an API key.")

    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.extraction_timeout_seconds,
        max_retries=0,
    )
    return OpenAIExtractionAdapter(
        client=client,
        model=settings.openai_model,
        image_detail=settings.openai_image_detail,
        service_tier=settings.openai_service_tier,
        max_output_tokens=settings.openai_max_output_tokens,
        timeout_seconds=settings.extraction_timeout_seconds,
    )
