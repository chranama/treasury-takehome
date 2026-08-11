from app.config import Settings
from app.extraction.contract import ExtractionAdapter
from app.extraction.fake import FakeExtractionAdapter, FakeExtractionScenario


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

    raise ExtractionConfigurationError(
        "The OpenAI extraction adapter is not implemented; live extraction is unavailable."
    )
