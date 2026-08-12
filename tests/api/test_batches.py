import asyncio
import csv
import time
from io import BytesIO, StringIO
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from app.batches import BATCH_TEMPLATE_HEADERS, PreflightIssueCode
from app.config import Settings
from app.db import connect
from app.extraction import (
    ExtractionError,
    ExtractionErrorKind,
    FakeExtractionAdapter,
    PreparedImage,
)
from app.main import create_app


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_path": tmp_path / "treasury.sqlite3",
        "temp_dir": tmp_path / "tmp",
        "batch_image_dir": tmp_path / "batch-images",
        "frontend_dist_path": tmp_path / "dist",
        "extraction_backend": "fake",
        "live_extraction_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def csv_bytes(rows: list[list[str]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(BATCH_TEMPLATE_HEADERS)
    writer.writerows(rows)
    return output.getvalue().encode()


def png_bytes(color: str = "navy") -> bytes:
    image = Image.new("RGB", (40, 24), color=color)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def package_files(
    rows: list[list[str]],
    images: list[tuple[str, bytes]],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("spreadsheet", ("batch.csv", csv_bytes(rows), "text/csv"))
    ]
    files.extend(("images", (name, content, "image/png")) for name, content in images)
    return files


def issue_codes(payload: dict) -> set[str]:
    return {issue["code"] for issue in payload["issues"]}


def wait_for_terminal(client: TestClient, batch_id: str) -> dict:
    for _ in range(200):
        payload = client.get(f"/api/batches/{batch_id}").json()
        if payload["state"] in {"completed", "interrupted"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("batch did not reach a terminal state")


class TrackingAdapter:
    def __init__(self, *, fail_calls: set[int] | None = None, delay: float = 0) -> None:
        self.delegate = FakeExtractionAdapter()
        self.fail_calls = fail_calls or set()
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def extract(self, image: PreparedImage):
        self.calls += 1
        call = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if call in self.fail_calls:
                raise ExtractionError(
                    kind=ExtractionErrorKind.UNAVAILABLE,
                    safe_message="provider unavailable",
                    retryable=False,
                )
            return await self.delegate.extract(image)
        finally:
            self.active -= 1


def test_downloadable_templates_have_stable_names_and_headers(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        csv_response = client.get("/api/batch-template.csv")
        xlsx_response = client.get("/api/batch-template.xlsx")

    assert csv_response.status_code == 200
    assert csv_response.headers["content-disposition"] == (
        'attachment; filename="label-review-batch.csv"'
    )
    assert next(csv.reader(StringIO(csv_response.text))) == list(BATCH_TEMPLATE_HEADERS)
    assert xlsx_response.status_code == 200
    assert xlsx_response.headers["content-disposition"] == (
        'attachment; filename="label-review-batch.xlsx"'
    )
    workbook = load_workbook(BytesIO(xlsx_response.content), read_only=True)
    assert tuple(cell.value for cell in next(workbook["Batch"].iter_rows())) == (
        BATCH_TEMPLATE_HEADERS
    )
    workbook.close()


def test_valid_preflight_returns_a_refreshable_draft_and_case_detail(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    rows = [["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/batches/preflight",
            files=package_files(rows, [("label.png", png_bytes())]),
        )
        payload = response.json()
        batch_id = payload["batch_id"]
        case_id = payload["cases"][0]["case_id"]
        recovered = client.get(f"/api/batches/{batch_id}")
        detail = client.get(f"/api/batches/{batch_id}/cases/{case_id}")

    assert response.status_code == 201
    assert response.headers["location"] == f"/api/batches/{batch_id}"
    assert UUID(batch_id).version == 4
    assert payload["counts"]["ready"] == 1
    assert payload["counts"]["needs_correction"] == 0
    assert recovered.status_code == 200
    assert recovered.json() == payload
    assert detail.status_code == 200
    assert detail.json()["expected_input"] == {
        "brand_name": "Brand",
        "class_type": "Bourbon",
        "expected_abv": "45",
        "expected_net_contents": "750 mL",
    }
    assert detail.json()["normalized_expected"] is not None


def test_mixed_draft_correction_and_replacement_update_only_the_target_case(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    rows = [
        ["APP-1", "one.png", "Brand", "Bourbon", "45", "750 mL"],
        ["APP-2", "missing.png", "Brand", "Bourbon", "101", "750 mL"],
    ]
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/batches/preflight",
            files=package_files(rows, [("one.png", png_bytes("red"))]),
        ).json()
        batch_id = created["batch_id"]
        ready_case, correction_case = created["cases"]
        ready_before = client.get(f"/api/batches/{batch_id}/cases/{ready_case['case_id']}").json()

        corrected = client.patch(
            f"/api/batches/{batch_id}/cases/{correction_case['case_id']}",
            json={"expected_abv": "45%"},
        )
        replaced = client.put(
            f"/api/batches/{batch_id}/cases/{correction_case['case_id']}/image",
            files={"image": ("replacement.png", png_bytes("blue"), "image/png")},
        )
        refreshed = client.get(f"/api/batches/{batch_id}").json()
        ready_after = client.get(f"/api/batches/{batch_id}/cases/{ready_case['case_id']}").json()

    assert created["counts"] == {
        "total": 2,
        "needs_correction": 1,
        "ready": 1,
        "queued": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "interrupted": 0,
        "not_selected": 0,
    }
    assert corrected.status_code == 200
    assert corrected.json()["summary"]["state"] == "needs_correction"
    assert issue_codes(corrected.json()["summary"]) == {PreflightIssueCode.MISSING_IMAGE.value}
    assert replaced.status_code == 200
    assert replaced.json()["summary"]["state"] == "ready"
    assert replaced.json()["summary"]["label_image_filename"] == "replacement.png"
    assert refreshed["counts"]["ready"] == 2
    assert ready_after == ready_before


def test_structurally_invalid_and_entirely_invalid_packages_return_safe_issues(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    entirely_invalid_rows = [["", "missing.png", "", "", "101", "750 oz"]]
    with TestClient(create_app(settings)) as client:
        malformed = client.post(
            "/api/batches/preflight",
            files={"spreadsheet": ("batch.csv", b'"unterminated', "text/csv")},
        )
        entirely_invalid = client.post(
            "/api/batches/preflight",
            files=package_files(entirely_invalid_rows, []),
        )

    assert malformed.status_code == 422
    malformed_payload = malformed.json()
    assert issue_codes(malformed_payload) == {PreflightIssueCode.MALFORMED_SPREADSHEET.value}
    assert UUID(malformed_payload["correlation_id"]).version == 4
    assert entirely_invalid.status_code == 201
    invalid_payload = entirely_invalid.json()
    assert invalid_payload["counts"]["ready"] == 0
    assert invalid_payload["counts"]["needs_correction"] == 1
    assert {
        PreflightIssueCode.MISSING_APPLICATION_ID.value,
        PreflightIssueCode.MISSING_IMAGE.value,
        PreflightIssueCode.INVALID_BRAND.value,
        PreflightIssueCode.INVALID_CLASS_TYPE.value,
        PreflightIssueCode.INVALID_ABV.value,
        PreflightIssueCode.INVALID_NET_CONTENTS.value,
    } <= issue_codes(invalid_payload["cases"][0])


def test_missing_multipart_fields_return_batch_specific_safe_issues(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        missing_spreadsheet = client.post("/api/batches/preflight")

    assert missing_spreadsheet.status_code == 422
    assert issue_codes(missing_spreadsheet.json()) == {
        PreflightIssueCode.UNSUPPORTED_SPREADSHEET.value
    }


def test_invalid_replacement_preserves_the_draft_and_returns_row_issue(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    rows = [["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/batches/preflight",
            files=package_files(rows, [("label.png", png_bytes())]),
        ).json()
        batch_id = created["batch_id"]
        case_id = created["cases"][0]["case_id"]
        rejected = client.put(
            f"/api/batches/{batch_id}/cases/{case_id}/image",
            files={"image": ("broken.png", b"not an image", "image/png")},
        )
        recovered = client.get(f"/api/batches/{batch_id}")

    assert rejected.status_code == 422
    assert issue_codes(rejected.json()) == {PreflightIssueCode.UNSUPPORTED_IMAGE.value}
    assert recovered.status_code == 200
    assert recovered.json()["counts"]["ready"] == 1
    assert len(list(settings.batch_image_dir.iterdir())) == 1
    assert list(settings.temp_dir.iterdir()) == []


def test_unknown_malformed_and_cross_batch_ids_share_the_not_found_response(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    rows = [["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]
    with TestClient(create_app(settings)) as client:
        first = client.post(
            "/api/batches/preflight",
            files=package_files(rows, [("label.png", png_bytes("red"))]),
        ).json()
        second = client.post(
            "/api/batches/preflight",
            files=package_files(rows, [("label.png", png_bytes("blue"))]),
        ).json()
        responses = [
            client.get("/api/batches/not-a-uuid"),
            client.get(f"/api/batches/{first['batch_id']}/cases/not-a-uuid"),
            client.get(f"/api/batches/{first['batch_id']}/cases/{second['cases'][0]['case_id']}"),
        ]
        collection = client.get("/api/batches")

    assert collection.status_code == 404
    for response in responses:
        assert response.status_code == 404
        payload = response.json()
        assert payload["code"] == "batch_not_found"
        assert payload["message"] == "The requested batch is unavailable."
        assert UUID(payload["correlation_id"]).version == 4


def test_start_is_idempotent_and_each_internal_case_has_one_durable_attempt(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
        openai_transient_retries=0,
        live_daily_attempt_limit=10,
        live_cumulative_cost_limit_usd="1",
        live_attempt_reservation_usd="0.01",
        live_source_window_seconds=60,
        live_source_max_submissions=2,
    )
    adapter = TrackingAdapter(delay=0.02)
    rows = [
        [f"APP-{index}", f"label-{index}.png", "Brand", "Bourbon", "45", "750 mL"]
        for index in range(1, 4)
    ]
    images = [(f"label-{index}.png", png_bytes()) for index in range(1, 4)]

    with TestClient(create_app(settings, extraction_adapter=adapter)) as client:
        created = client.post(
            "/api/batches/preflight",
            files=package_files(rows, images),
        ).json()
        path = f"/api/batches/{created['batch_id']}/start"
        headers = {"Idempotency-Key": "one-stable-batch-key"}
        first = client.post(path, json={"selection": "all_cases"}, headers=headers)
        repeated = client.post(path, json={"selection": "all_cases"}, headers=headers)
        completed = wait_for_terminal(client, created["batch_id"])
        repeated_after_completion = client.post(
            path,
            json={"selection": "all_cases"},
            headers=headers,
        )
        conflicting = client.post(
            path,
            json={"selection": "all_cases"},
            headers={"Idempotency-Key": "a-different-batch-key"},
        )

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated_after_completion.status_code == 202
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "batch_state_conflict"
    assert completed["counts"]["completed"] == 3
    assert completed["counts"]["failed"] == 0
    assert adapter.calls == 3
    assert adapter.max_active == 2
    with connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_submissions").fetchone() == (3,)
        assert connection.execute("SELECT COUNT(*) FROM provider_attempts").fetchone() == (3,)


def test_ready_only_selection_is_explicit_and_case_failure_is_isolated(tmp_path: Path) -> None:
    adapter = TrackingAdapter(fail_calls={1}, delay=0.01)
    rows = [
        ["APP-1", "one.png", "Brand", "Bourbon", "45", "750 mL"],
        ["APP-2", "two.png", "Brand", "Bourbon", "45", "750 mL"],
        ["APP-3", "missing.png", "Brand", "Bourbon", "101", "750 mL"],
    ]
    with TestClient(create_app(make_settings(tmp_path), extraction_adapter=adapter)) as client:
        created = client.post(
            "/api/batches/preflight",
            files=package_files(
                rows,
                [("one.png", png_bytes("red")), ("two.png", png_bytes("blue"))],
            ),
        ).json()
        path = f"/api/batches/{created['batch_id']}/start"
        rejected = client.post(
            path,
            json={"selection": "all_cases"},
            headers={"Idempotency-Key": "all-cases-with-errors"},
        )
        started = client.post(
            path,
            json={"selection": "ready_cases_only"},
            headers={"Idempotency-Key": "ready-cases-only-key"},
        )
        completed = wait_for_terminal(client, created["batch_id"])

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "batch_has_corrections"
    assert started.status_code == 202
    assert completed["state"] == "completed"
    assert completed["counts"]["completed"] == 1
    assert completed["counts"]["failed"] == 1
    assert completed["counts"]["not_selected"] == 1
    assert adapter.calls == 2
    assert not list(make_settings(tmp_path).batch_image_dir.glob("*.png"))


def test_capacity_exhaustion_creates_per_case_failures_without_unaccounted_requests(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
        openai_transient_retries=0,
        live_daily_attempt_limit=1,
        live_cumulative_cost_limit_usd="1",
        live_attempt_reservation_usd="0.01",
        live_source_window_seconds=60,
        live_source_max_submissions=10,
    )
    adapter = TrackingAdapter(delay=0.01)
    rows = [
        [f"APP-{index}", f"label-{index}.png", "Brand", "Bourbon", "45", "750 mL"]
        for index in range(1, 4)
    ]
    images = [(f"label-{index}.png", png_bytes()) for index in range(1, 4)]

    with TestClient(create_app(settings, extraction_adapter=adapter)) as client:
        created = client.post(
            "/api/batches/preflight",
            files=package_files(rows, images),
        ).json()
        started = client.post(
            f"/api/batches/{created['batch_id']}/start",
            json={"selection": "all_cases"},
            headers={"Idempotency-Key": "capacity-batch-key"},
        )
        completed = wait_for_terminal(client, created["batch_id"])

    assert started.status_code == 202
    assert completed["counts"]["completed"] == 1
    assert completed["counts"]["failed"] == 2
    assert {case["short_reason"] for case in completed["cases"] if case["state"] == "failed"} == {
        "Live review capacity is temporarily unavailable."
    }
    assert adapter.calls == 1
    with connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM provider_attempts").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM review_submissions "
            "WHERE status = 'failed' AND error_kind = 'capacity_reached'"
        ).fetchone() == (2,)


def test_startup_marks_uncertain_batch_work_interrupted_without_replay(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        extraction_backend="openai",
        live_extraction_enabled=True,
        openai_api_key="test-key",
        openai_transient_retries=0,
        live_daily_attempt_limit=10,
        live_cumulative_cost_limit_usd="1",
        live_attempt_reservation_usd="0.01",
        live_source_window_seconds=60,
        live_source_max_submissions=10,
    )
    rows = [["APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL"]]
    with TestClient(create_app(settings, extraction_adapter=TrackingAdapter())) as client:
        created = client.post(
            "/api/batches/preflight",
            files=package_files(rows, [("label.png", png_bytes())]),
        ).json()

    case_id = created["cases"][0]["case_id"]
    with connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE batch_reviews SET status = 'processing' WHERE batch_id = ?",
            (created["batch_id"],),
        )
        connection.execute(
            """
            INSERT INTO review_submissions (
                idempotency_hash, correlation_id, status, created_at
            ) VALUES ('uncertain-hash', ?, 'processing', '2026-08-12T12:00:00+00:00')
            """,
            (case_id,),
        )
        connection.execute(
            """
            INSERT INTO provider_attempts (
                correlation_id, attempt_number, status, reserved_at,
                reserved_cost_units, model, prompt_revision, image_detail,
                requested_service_tier
            ) VALUES (?, 1, 'reserved', '2026-08-12T12:00:00+00:00',
                      1000000, 'gpt-5.6-luna', 'label-observations-v2', 'high', 'default')
            """,
            (case_id,),
        )
        connection.execute(
            "UPDATE batch_cases SET status = 'processing', provider_correlation_id = ? "
            "WHERE case_id = ?",
            (case_id, case_id),
        )
        connection.execute(
            "UPDATE batch_images SET status = 'processing' WHERE batch_id = ?",
            (created["batch_id"],),
        )

    adapter = TrackingAdapter()
    with TestClient(create_app(settings, extraction_adapter=adapter)) as client:
        reconciled = client.get(f"/api/batches/{created['batch_id']}").json()

    assert reconciled["state"] == "interrupted"
    assert reconciled["counts"]["interrupted"] == 1
    assert adapter.calls == 0
    assert not list(settings.batch_image_dir.glob("*.png"))
    with connect(settings.database_path) as connection:
        assert connection.execute(
            "SELECT status, error_kind FROM review_submissions WHERE correlation_id = ?",
            (case_id,),
        ).fetchone() == ("failed", "interrupted")
        assert connection.execute(
            "SELECT status, error_kind FROM provider_attempts WHERE correlation_id = ?",
            (case_id,),
        ).fetchone() == ("failed", "interrupted")


def test_graceful_shutdown_bounds_drain_and_interrupts_remaining_cases(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    adapter = TrackingAdapter(delay=1)
    application = create_app(settings, extraction_adapter=adapter)
    application.state.batch_processing_service.drain_seconds = 0.01
    rows = [
        ["APP-1", "one.png", "Brand", "Bourbon", "45", "750 mL"],
        ["APP-2", "two.png", "Brand", "Bourbon", "45", "750 mL"],
    ]

    with TestClient(application) as client:
        created = client.post(
            "/api/batches/preflight",
            files=package_files(
                rows,
                [("one.png", png_bytes("red")), ("two.png", png_bytes("blue"))],
            ),
        ).json()
        client.post(
            f"/api/batches/{created['batch_id']}/start",
            json={"selection": "all_cases"},
            headers={"Idempotency-Key": "shutdown-drain-key"},
        )
        for _ in range(100):
            active = client.get(f"/api/batches/{created['batch_id']}").json()
            if active["counts"]["processing"]:
                break
            time.sleep(0.005)

    with connect(settings.database_path) as connection:
        assert connection.execute(
            "SELECT status FROM batch_reviews WHERE batch_id = ?",
            (created["batch_id"],),
        ).fetchone() == ("interrupted",)
        assert connection.execute(
            "SELECT COUNT(*) FROM batch_cases WHERE batch_id = ? AND status = 'interrupted'",
            (created["batch_id"],),
        ).fetchone() == (2,)
    assert not list(settings.batch_image_dir.glob("*.png"))
