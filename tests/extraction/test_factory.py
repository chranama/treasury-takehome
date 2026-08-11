import asyncio

import pytest

from app.config import Settings
from app.extraction import (
    ExtractionAdapter,
    ExtractionConfigurationError,
    FakeExtractionAdapter,
    FakeExtractionScenario,
    OpenAIExtractionAdapter,
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


def test_factory_builds_openai_adapter_without_sdk_retries() -> None:
    settings = Settings(
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
    )

    adapter = create_extraction_adapter(settings)

    assert isinstance(adapter, OpenAIExtractionAdapter)
    assert adapter.model == "gpt-5.6-luna"
    assert adapter.image_detail == "high"
    assert adapter.service_tier == "default"
    assert adapter.max_output_tokens == 1_000
    assert adapter.client.max_retries == 0
    asyncio.run(adapter.client.close())


def test_factory_requires_key_for_openai_without_substituting_fake() -> None:
    settings = Settings(
        _env_file=None,
        extraction_backend="openai",
        live_extraction_enabled=True,
    )

    with pytest.raises(ExtractionConfigurationError, match="requires an API key"):
        create_extraction_adapter(settings)
