import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.errors import ReviewApiError
from app.comparison import (
    ApplicationErrorCategory,
    CheckName,
    ExtractionObservations,
    OverallOutcome,
)
from app.config import Settings
from app.db import connect
from app.extraction import (
    ExtractionError,
    ExtractionErrorKind,
    FakeExtractionAdapter,
    FakeExtractionFailure,
    FakeExtractionScenario,
    OpenAIExtractionResult,
    OpenAIUsage,
    PreparedImage,
)
from app.main import create_app
from app.reviews import AttemptRejected, AttemptRejectionKind


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_path": tmp_path / "treasury.sqlite3",
        "temp_dir": tmp_path / "tmp",
        "batch_image_dir": tmp_path / "batch-images",
        "frontend_dist_path": tmp_path / "dist",
        "extraction_backend": "fake",
        "live_extraction_enabled": False,
        "extraction_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def png_bytes() -> bytes:
    image = Image.new("RGB", (40, 24), color=(30, 80, 130))
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def review_form(**overrides: str) -> dict[str, str]:
    values = {
        "brand_name": "OLD TOM",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "expected_abv": "45",
        "expected_net_contents": "750",
        "expected_net_contents_unit": "mL",
    }
    values.update(overrides)
    return values


def image_file(
    content: bytes | None = None,
    *,
    filename: str = "private-label-name.png",
    content_type: str = "image/png",
) -> tuple[str, tuple[str, bytes, str]]:
    return (
        "image",
        (filename, content if content is not None else png_bytes(), content_type),
    )


class RecordingAdapter:
    def __init__(
        self,
        scenario: FakeExtractionScenario = FakeExtractionScenario.CLEAR_MATCHING_LABEL,
    ) -> None:
        self.delegate = FakeExtractionAdapter(scenario=scenario)
        self.calls = 0
        self.prepared_paths: list[Path] = []

    async def extract(self, image: PreparedImage) -> ExtractionObservations:
        self.calls += 1
        self.prepared_paths.append(image.path)
        assert image.path.is_file()
        return await self.delegate.extract(image)


class SlowAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.prepared_path: Path | None = None

    async def extract(self, image: PreparedImage) -> ExtractionObservations:
        self.calls += 1
        self.prepared_path = image.path
        await asyncio.sleep(0.1)
        return await FakeExtractionAdapter().extract(image)


class MalformedAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.prepared_path: Path | None = None

    async def extract(self, image: PreparedImage) -> ExtractionObservations:
        self.calls += 1
        self.prepared_path = image.path
        return {"provider_payload": "not observations"}  # type: ignore[return-value]


class ExplodingAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.prepared_path: Path | None = None

    async def extract(self, image: PreparedImage) -> ExtractionObservations:
        self.calls += 1
        self.prepared_path = image.path
        raise RuntimeError("secret provider payload and API key")


class ExtractionFailureAdapter:
    def __init__(self, failure: FakeExtractionFailure) -> None:
        self.delegate = FakeExtractionAdapter(failure=failure)
        self.calls = 0
        self.prepared_path: Path | None = None

    async def extract(self, image: PreparedImage) -> ExtractionObservations:
        self.calls += 1
        self.prepared_path = image.path
        return await self.delegate.extract(image)


class MeteredSequenceAdapter:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0

    async def extract_with_metadata(self, image: PreparedImage) -> OpenAIExtractionResult:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ExtractionError(
                kind=ExtractionErrorKind.TRANSIENT_FAILURE,
                safe_message="temporary provider failure",
                retryable=True,
            )
        observations = await FakeExtractionAdapter().extract(image)
        return OpenAIExtractionResult(
            observations=observations,
            provider_request_id=f"resp_{self.calls}",
            model="gpt-5.6-luna",
            prompt_revision="label-observations-v2",
            image_detail="high",
            requested_service_tier="default",
            response_service_tier="default",
            attempt_count=1,
            latency_ms=100,
            usage=OpenAIUsage(
                input_tokens=3020,
                cached_input_tokens=3017,
                output_tokens=240,
                reasoning_tokens=0,
                total_tokens=3260,
            ),
        )


class RecordingGate:
    def __init__(self) -> None:
        self.correlation_ids: list[str] = []
        self.exits = 0

    @asynccontextmanager
    async def submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        source_identity: str,
    ) -> AsyncGenerator["RecordingSubmission", None]:
        del idempotency_key, source_identity
        self.correlation_ids.append(correlation_id)
        try:
            yield RecordingSubmission()
        finally:
            self.exits += 1


class RecordingReservation:
    async def settle_success(self, success=None) -> None:
        del success

    async def settle_failure(self, error_kind: str) -> None:
        del error_kind


class RecordingSubmission:
    @asynccontextmanager
    async def reserve_attempt(self) -> AsyncGenerator[RecordingReservation, None]:
        yield RecordingReservation()

    async def complete(self, **_: object) -> None:
        return None

    async def fail(self, error_kind: str) -> None:
        del error_kind


class RejectingGate:
    def __init__(self, kind: AttemptRejectionKind) -> None:
        self.kind = kind
        self.calls = 0

    @asynccontextmanager
    async def submission(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        source_identity: str,
    ) -> AsyncGenerator[RecordingSubmission, None]:
        del correlation_id, idempotency_key, source_identity
        self.calls += 1
        raise AttemptRejected(self.kind)
        yield


def post_review(
    client: TestClient,
    *,
    data: dict[str, str] | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    idempotency_key: str | None = None,
):
    return client.post(
        "/api/reviews",
        data=data,
        files=files,
        headers={"Idempotency-Key": idempotency_key or str(uuid4())},
    )


def assert_error_contract(response: Any, expected_category: str) -> dict[str, object]:
    payload = response.json()
    assert payload["category"] == expected_category
    assert UUID(payload["correlation_id"])
    assert payload["processing_duration_ms"] >= 0
    assert response.headers["x-correlation-id"] == payload["correlation_id"]
    return payload


def test_review_api_error_requires_a_bounded_safe_message() -> None:
    with pytest.raises(ValueError, match="safe message"):
        ReviewApiError(ApplicationErrorCategory.INTERNAL_ERROR, "   ")


@pytest.mark.parametrize(
    ("scenario", "expected_outcome", "affected_check"),
    [
        (
            FakeExtractionScenario.CLEAR_MATCHING_LABEL,
            OverallOutcome.ALL_CHECKS_PASSED,
            None,
        ),
        (
            FakeExtractionScenario.MISMATCHED_NET_CONTENTS,
            OverallOutcome.NEEDS_REVIEW,
            CheckName.NET_CONTENTS,
        ),
        (
            FakeExtractionScenario.UNREADABLE_IMAGE,
            OverallOutcome.NEEDS_REVIEW,
            CheckName.GOVERNMENT_WARNING,
        ),
    ],
)
def test_complete_review_request_uses_fake_adapter_and_stable_response(
    scenario: FakeExtractionScenario,
    expected_outcome: OverallOutcome,
    affected_check: CheckName | None,
    tmp_path: Path,
) -> None:
    adapter = RecordingAdapter(scenario)
    gate = RecordingGate()
    app = create_app(
        make_settings(tmp_path),
        extraction_adapter=adapter,
        attempt_gate=gate,
    )

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == expected_outcome.value
    assert payload["processing_mode"] == "synthetic"
    assert len(payload["checks"]) == 5
    assert payload["processing_duration_ms"] >= 0
    assert UUID(payload["correlation_id"])
    assert response.headers["x-correlation-id"] == payload["correlation_id"]
    assert gate.correlation_ids == [payload["correlation_id"]]
    assert gate.exits == 1
    assert adapter.calls == 1
    assert all(not path.exists() for path in adapter.prepared_paths)
    assert list(make_settings(tmp_path).temp_dir.iterdir()) == []
    if affected_check is not None:
        checks = {check["name"]: check for check in payload["checks"]}
        assert checks[affected_check.value]["status"] != "match"


@pytest.mark.parametrize(
    ("form_overrides", "expected_message_fragment"),
    [
        ({"brand_name": "   "}, "expected application values"),
        ({"expected_abv": "101"}, "required fields"),
        ({"expected_net_contents": "0"}, "required fields"),
        ({"expected_net_contents_unit": "ounces"}, "required fields"),
    ],
)
def test_invalid_expected_fields_return_safe_validation_error_without_extraction(
    form_overrides: dict[str, str],
    expected_message_fragment: str,
    tmp_path: Path,
) -> None:
    adapter = RecordingAdapter()
    app = create_app(make_settings(tmp_path), extraction_adapter=adapter)

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(**form_overrides),
            files=[image_file()],
        )

    assert response.status_code == 422
    payload = assert_error_contract(response, "invalid_input")
    assert expected_message_fragment in str(payload["message"])
    assert adapter.calls == 0
    assert "101" not in str(payload["message"])
    assert "ounces" not in str(payload["message"])


def test_exactly_one_image_is_required(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    app = create_app(make_settings(tmp_path), extraction_adapter=adapter)

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file(filename="one.png"), image_file(filename="two.png")],
        )

    assert response.status_code == 422
    payload = assert_error_contract(response, "invalid_input")
    assert payload["message"] == "Submit exactly one label image or composite."
    assert adapter.calls == 0
    assert "one.png" not in response.text
    assert "two.png" not in response.text


def test_idempotency_key_is_required_before_extraction(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    app = create_app(make_settings(tmp_path), extraction_adapter=adapter)

    with TestClient(app) as client:
        response = client.post(
            "/api/reviews",
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 422
    assert_error_contract(response, "invalid_input")
    assert adapter.calls == 0


def test_fake_duplicate_idempotency_key_never_extracts_twice(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    app = create_app(make_settings(tmp_path), extraction_adapter=adapter)
    idempotency_key = "550e8400-e29b-41d4-a716-446655440000"

    with TestClient(app) as client:
        first = post_review(
            client,
            data=review_form(),
            files=[image_file()],
            idempotency_key=idempotency_key,
        )
        duplicate = post_review(
            client,
            data=review_form(),
            files=[image_file()],
            idempotency_key=idempotency_key,
        )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert_error_contract(duplicate, "duplicate_submission")
    assert adapter.calls == 1


def test_invalid_image_is_rejected_before_attempt_reservation(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    gate = RecordingGate()
    app = create_app(
        make_settings(tmp_path),
        extraction_adapter=adapter,
        attempt_gate=gate,
    )

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file(b"\x89PNG\r\n\x1a\nprivate-corrupt-content")],
        )

    assert response.status_code == 422
    payload = assert_error_contract(response, "invalid_input")
    assert payload["message"] == "The image could not be decoded. Choose a valid image."
    assert adapter.calls == 0
    assert gate.correlation_ids == []
    assert "private-corrupt-content" not in response.text
    assert list(make_settings(tmp_path).temp_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("kind", "status_code", "category"),
    [
        (AttemptRejectionKind.CAPACITY_REACHED, 503, "capacity_reached"),
        (AttemptRejectionKind.TRAFFIC_THROTTLED, 429, "traffic_throttled"),
        (AttemptRejectionKind.DUPLICATE_SUBMISSION, 409, "duplicate_submission"),
    ],
)
def test_attempt_gate_rejections_are_distinct_and_do_not_invoke_extraction(
    kind: AttemptRejectionKind,
    status_code: int,
    category: str,
    tmp_path: Path,
) -> None:
    adapter = RecordingAdapter()
    gate = RejectingGate(kind)
    app = create_app(
        make_settings(tmp_path),
        extraction_adapter=adapter,
        attempt_gate=gate,
    )

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == status_code
    assert_error_contract(response, category)
    assert gate.calls == 1
    assert adapter.calls == 0
    assert list(make_settings(tmp_path).temp_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("settings_overrides", "expected_message"),
    [
        (
            {
                "extraction_backend": "openai",
                "live_extraction_enabled": False,
            },
            "Live label extraction is disabled.",
        ),
        (
            {
                "app_env": "production",
                "extraction_backend": "fake",
            },
            "Live label extraction is not available.",
        ),
    ],
)
def test_unavailable_modes_never_run_the_adapter(
    settings_overrides: dict[str, object],
    expected_message: str,
    tmp_path: Path,
) -> None:
    adapter = RecordingAdapter()
    gate = RecordingGate()
    app = create_app(
        make_settings(tmp_path, **settings_overrides),
        extraction_adapter=adapter,
        attempt_gate=gate,
    )

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 503
    payload = assert_error_contract(response, "live_extraction_disabled")
    assert payload["message"] == expected_message
    assert adapter.calls == 0
    assert gate.correlation_ids == []
    assert list(make_settings(tmp_path).temp_dir.iterdir()) == []


def test_live_adapter_requires_an_explicit_attempt_gate(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
    )
    app = create_app(settings, extraction_adapter=adapter)

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 503
    assert_error_contract(response, "live_extraction_disabled")
    assert adapter.calls == 0


def test_openai_configuration_never_falls_back_to_fake_adapter(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 503
    payload = assert_error_contract(response, "live_extraction_disabled")
    assert payload["message"] == "Live label extraction is not available."


def test_timeout_is_bounded_and_cleans_the_prepared_image(tmp_path: Path) -> None:
    adapter = SlowAdapter()
    settings = make_settings(tmp_path, extraction_timeout_seconds=0.01)
    app = create_app(settings, extraction_adapter=adapter)

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 504
    assert_error_contract(response, "provider_timeout")
    assert adapter.calls == 1
    assert adapter.prepared_path is not None
    assert not adapter.prepared_path.exists()
    assert list(settings.temp_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "status_code", "category"),
    [
        (FakeExtractionFailure.TIMEOUT, 504, "provider_timeout"),
        (FakeExtractionFailure.MALFORMED_OUTPUT, 502, "malformed_provider_output"),
        (FakeExtractionFailure.TRANSIENT_FAILURE, 502, "provider_unavailable"),
        (FakeExtractionFailure.UNAVAILABLE, 502, "provider_unavailable"),
        (FakeExtractionFailure.INTERNAL_FAILURE, 500, "internal_error"),
    ],
)
def test_bounded_extraction_errors_map_to_safe_api_errors_without_retry(
    failure: FakeExtractionFailure,
    status_code: int,
    category: str,
    tmp_path: Path,
) -> None:
    adapter = ExtractionFailureAdapter(failure)
    gate = RecordingGate()
    app = create_app(
        make_settings(tmp_path),
        extraction_adapter=adapter,
        attempt_gate=gate,
    )

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == status_code
    assert_error_contract(response, category)
    assert adapter.calls == 1
    assert gate.exits == 1
    assert adapter.prepared_path is not None
    assert not adapter.prepared_path.exists()


def test_malformed_adapter_return_is_not_exposed(tmp_path: Path) -> None:
    adapter = MalformedAdapter()
    app = create_app(make_settings(tmp_path), extraction_adapter=adapter)

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 502
    assert_error_contract(response, "malformed_provider_output")
    assert "provider_payload" not in response.text
    assert adapter.calls == 1
    assert adapter.prepared_path is not None
    assert not adapter.prepared_path.exists()


def test_unexpected_adapter_exception_returns_safe_internal_error(tmp_path: Path) -> None:
    adapter = ExplodingAdapter()
    app = create_app(make_settings(tmp_path), extraction_adapter=adapter)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file(filename="secret-application.png")],
        )

    assert response.status_code == 500
    assert_error_contract(response, "internal_error")
    assert "secret provider payload" not in response.text
    assert "API key" not in response.text
    assert "secret-application.png" not in response.text
    assert adapter.calls == 1
    assert adapter.prepared_path is not None
    assert not adapter.prepared_path.exists()


def live_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "extraction_backend": "openai",
        "live_extraction_enabled": True,
        "openai_api_key": "test-key",
        "openai_transient_retries": 1,
        "live_daily_attempt_limit": 10,
        "live_cumulative_cost_limit_usd": Decimal("1"),
        "live_attempt_reservation_usd": Decimal("0.01"),
        "live_source_window_seconds": 60,
        "live_source_max_submissions": 10,
    }
    values.update(overrides)
    return make_settings(tmp_path, **values)


def test_live_duplicate_idempotency_key_never_creates_second_provider_attempt(
    tmp_path: Path,
) -> None:
    adapter = MeteredSequenceAdapter()
    settings = live_settings(tmp_path)
    app = create_app(settings, extraction_adapter=adapter)
    idempotency_key = "550e8400-e29b-41d4-a716-446655440000"

    with TestClient(app) as client:
        first = post_review(
            client,
            data=review_form(),
            files=[image_file()],
            idempotency_key=idempotency_key,
        )
        duplicate = post_review(
            client,
            data=review_form(),
            files=[image_file()],
            idempotency_key=idempotency_key,
        )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert_error_contract(duplicate, "duplicate_submission")
    assert adapter.calls == 1
    with connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM provider_attempts").fetchone() == (1,)
    database_bytes = settings.database_path.read_bytes()
    assert idempotency_key.encode("ascii") not in database_bytes
    assert b"OLD TOM" not in database_bytes
    assert b"GOVERNMENT WARNING" not in database_bytes


def test_live_retry_receives_a_second_durable_reservation(tmp_path: Path) -> None:
    adapter = MeteredSequenceAdapter(fail_first=True)
    settings = live_settings(tmp_path)
    app = create_app(settings, extraction_adapter=adapter)

    with TestClient(app) as client:
        response = post_review(
            client,
            data=review_form(),
            files=[image_file()],
        )

    assert response.status_code == 200
    assert adapter.calls == 2
    with connect(settings.database_path) as connection:
        attempts = connection.execute(
            """
            SELECT attempt_number, status, error_kind, provider_request_id
            FROM provider_attempts ORDER BY attempt_number
            """
        ).fetchall()
    assert attempts == [
        (1, "failed", "transient_failure", None),
        (2, "succeeded", None, "resp_2"),
    ]
