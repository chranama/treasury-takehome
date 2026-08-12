import csv
from io import BytesIO, StringIO
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from app.batches import BATCH_TEMPLATE_HEADERS, PreflightIssueCode
from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_path=tmp_path / "treasury.sqlite3",
        temp_dir=tmp_path / "tmp",
        batch_image_dir=tmp_path / "batch-images",
        frontend_dist_path=tmp_path / "dist",
        extraction_backend="fake",
        live_extraction_enabled=False,
    )


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
