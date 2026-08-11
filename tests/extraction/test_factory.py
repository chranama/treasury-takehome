import pytest

from app.config import Settings
from app.extraction import (
    ExtractionAdapter,
    ExtractionConfigurationError,
    FakeExtractionAdapter,
    FakeExtractionScenario,
    create_extraction_adapter,
)


def test_factory_selects_configured_fake_scenario() -> None:
    settings = Settings(
        extraction_backend="fake",
        fake_extraction_scenario=FakeExtractionScenario.ALTERED_WARNING_TEXT.value,
    )

    adapter = create_extraction_adapter(settings)

    assert isinstance(adapter, ExtractionAdapter)
    assert isinstance(adapter, FakeExtractionAdapter)
    assert adapter.scenario == FakeExtractionScenario.ALTERED_WARNING_TEXT


def test_factory_rejects_unknown_fake_scenario() -> None:
    settings = Settings(
        extraction_backend="fake",
        fake_extraction_scenario="not-a-scenario",
    )

    with pytest.raises(ExtractionConfigurationError, match="Unknown fake extraction scenario"):
        create_extraction_adapter(settings)


def test_factory_never_substitutes_fake_for_openai() -> None:
    settings = Settings(extraction_backend="openai", live_extraction_enabled=False)

    with pytest.raises(ExtractionConfigurationError, match="OpenAI extraction adapter"):
        create_extraction_adapter(settings)
