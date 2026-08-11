import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.comparison import (
    ExtractionObservations,
    FieldObservation,
    Readability,
    TextCandidate,
    TextWeight,
    Visibility,
    WarningObservation,
)
from app.extraction import (
    EXTRACTION_INSTRUCTIONS,
    PROMPT_REVISION,
    ExtractionError,
    ExtractionErrorKind,
    ImageMediaType,
    OpenAIExtractionAdapter,
    PreparedImage,
)


def observations() -> ExtractionObservations:
    field = FieldObservation(
        candidates=[TextCandidate(text="Visible text")],
        visibility=Visibility.VISIBLE,
        readability=Readability.READABLE,
    )
    return ExtractionObservations(
        brand_name=field,
        class_type=field,
        alcohol_content=field,
        net_contents=field,
        government_warning=WarningObservation(
            text="Visible warning",
            heading_text="GOVERNMENT WARNING:",
            heading_weight=TextWeight.BOLD,
            body_weight=TextWeight.NOT_BOLD,
            visibility=Visibility.VISIBLE,
            readability=Readability.READABLE,
        ),
    )


def response(*, parsed: ExtractionObservations | None = None, status: str = "completed") -> object:
    usage = SimpleNamespace(
        input_tokens=800,
        input_tokens_details=SimpleNamespace(cached_tokens=100),
        output_tokens=200,
        output_tokens_details=SimpleNamespace(reasoning_tokens=25),
        total_tokens=1_000,
    )
    return SimpleNamespace(
        output_parsed=parsed if parsed is not None else observations(),
        status=status,
        id="resp_test",
        model="gpt-5.6-luna",
        usage=usage,
        service_tier="default",
    )


def adapter_with_parse(parse: AsyncMock, **overrides: object) -> OpenAIExtractionAdapter:
    client = cast(AsyncOpenAI, SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    values = {
        "client": client,
        "model": "gpt-5.6-luna",
        "image_detail": "high",
        "service_tier": "default",
        "max_output_tokens": 1_000,
        "timeout_seconds": 12.0,
        "transient_retries": 1,
        "retry_delay_seconds": 0.0,
        **overrides,
    }
    return OpenAIExtractionAdapter(**values)  # type: ignore[arg-type]


@pytest.fixture
def prepared_image(tmp_path: Path) -> PreparedImage:
    path = tmp_path / "prepared.png"
    path.write_bytes(b"normalized image bytes")
    return PreparedImage(
        path=path,
        media_type=ImageMediaType.PNG,
        width=1_200,
        height=800,
        byte_count=path.stat().st_size,
    )


def test_sends_one_image_with_bounded_structured_response(
    prepared_image: PreparedImage,
) -> None:
    parse = AsyncMock(return_value=response())
    adapter = adapter_with_parse(parse)

    extracted = asyncio.run(adapter.extract_with_metadata(prepared_image))

    assert extracted.observations == observations()
    assert extracted.provider_request_id == "resp_test"
    assert extracted.model == "gpt-5.6-luna"
    assert extracted.prompt_revision == PROMPT_REVISION
    assert extracted.image_detail == "high"
    assert extracted.requested_service_tier == "default"
    assert extracted.response_service_tier == "default"
    assert extracted.attempt_count == 1
    assert extracted.usage is not None
    assert extracted.usage.input_tokens == 800
    assert extracted.usage.cached_input_tokens == 100
    assert extracted.usage.output_tokens == 200
    assert extracted.usage.reasoning_tokens == 25

    request = parse.await_args.kwargs
    assert request["model"] == "gpt-5.6-luna"
    assert request["instructions"] == EXTRACTION_INSTRUCTIONS
    assert PROMPT_REVISION == "label-observations-v2"
    assert "Use not_visible only when image quality is sufficient" in request["instructions"]
    assert request["text_format"] is ExtractionObservations
    assert request["tools"] == []
    assert request["reasoning"] == {"effort": "none"}
    assert request["service_tier"] == "default"
    assert request["max_output_tokens"] == 1_000
    assert request["store"] is False
    assert request["timeout"] == 12.0

    content = request["input"][0]["content"]
    assert len(content) == 2
    assert content[1]["type"] == "input_image"
    assert content[1]["detail"] == "high"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    request_text = f"{request['instructions']} {content[0]['text']}"
    assert "Treasury Reserve" not in request_text
    assert "Kentucky Straight Bourbon Whiskey" not in request_text
    assert "750 mL" not in request_text
    assert prepared_image.path.name not in request_text


def test_extract_returns_only_observations(prepared_image: PreparedImage) -> None:
    adapter = adapter_with_parse(AsyncMock(return_value=response()))

    extracted = asyncio.run(adapter.extract(prepared_image))

    assert isinstance(extracted, ExtractionObservations)


def test_retries_one_connection_failure(prepared_image: PreparedImage) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    parse = AsyncMock(
        side_effect=[
            APIConnectionError(message="connection reset", request=request),
            response(),
        ]
    )
    adapter = adapter_with_parse(parse)

    extracted = asyncio.run(adapter.extract_with_metadata(prepared_image))

    assert extracted.attempt_count == 2
    assert parse.await_count == 2


def test_retries_one_provider_server_failure(prepared_image: PreparedImage) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response_500 = httpx.Response(500, request=request)
    parse = AsyncMock(
        side_effect=[
            InternalServerError("provider unavailable", response=response_500, body=None),
            response(),
        ]
    )
    adapter = adapter_with_parse(parse)

    extracted = asyncio.run(adapter.extract_with_metadata(prepared_image))

    assert extracted.attempt_count == 2
    assert parse.await_count == 2


def test_does_not_retry_timeout(prepared_image: PreparedImage) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    parse = AsyncMock(side_effect=APITimeoutError(request))
    adapter = adapter_with_parse(parse)

    with pytest.raises(ExtractionError) as captured:
        asyncio.run(adapter.extract(prepared_image))

    assert captured.value.kind == ExtractionErrorKind.TIMEOUT
    assert captured.value.retryable is False
    assert parse.await_count == 1


def test_does_not_retry_rate_limit(prepared_image: PreparedImage) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response_429 = httpx.Response(429, request=request)
    parse = AsyncMock(side_effect=RateLimitError("rate limited", response=response_429, body=None))
    adapter = adapter_with_parse(parse)

    with pytest.raises(ExtractionError) as captured:
        asyncio.run(adapter.extract(prepared_image))

    assert captured.value.kind == ExtractionErrorKind.TRANSIENT_FAILURE
    assert captured.value.retryable is True
    assert parse.await_count == 1


def test_rejects_incomplete_or_unparsed_response(prepared_image: PreparedImage) -> None:
    incomplete = response(status="incomplete")
    incomplete.output_parsed = None  # type: ignore[attr-defined]
    adapter = adapter_with_parse(AsyncMock(return_value=incomplete))

    with pytest.raises(ExtractionError) as captured:
        asyncio.run(adapter.extract(prepared_image))

    assert captured.value.kind == ExtractionErrorKind.MALFORMED_OUTPUT
    assert captured.value.retryable is False


def test_missing_prepared_image_is_safe_internal_failure(tmp_path: Path) -> None:
    missing = PreparedImage(
        path=tmp_path / "missing.png",
        media_type=ImageMediaType.PNG,
        width=100,
        height=100,
        byte_count=100,
    )
    adapter = adapter_with_parse(AsyncMock())

    with pytest.raises(ExtractionError) as captured:
        asyncio.run(adapter.extract(missing))

    assert captured.value.kind == ExtractionErrorKind.INTERNAL_FAILURE
    assert "missing.png" not in captured.value.safe_message


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"image_detail": "auto"}, "image detail"),
        ({"service_tier": "priority"}, "service tier"),
        ({"max_output_tokens": 100}, "maximum output tokens"),
        ({"timeout_seconds": 0}, "timeout"),
        ({"transient_retries": 2}, "transient retries"),
        ({"retry_delay_seconds": -1}, "retry delay"),
    ],
)
def test_adapter_configuration_is_bounded(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        adapter_with_parse(AsyncMock(), **overrides)
