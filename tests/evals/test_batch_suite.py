import asyncio
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.batches import prepare_batch_preflight
from app.config import PROJECT_ROOT
from evals.batch_suite import (
    build_manifest,
    generate_package,
    materialize_case,
    materialize_manifest,
    write_manifest,
)
from evals.manifest import SpreadsheetFormat, load_manifest_v2

COMMITTED_MANIFEST = PROJECT_ROOT / "fixtures" / "p1-packages-v1.json"


def _upload(content: bytes, filename: str) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename, size=len(content))


def _all_issue_codes(preflight) -> set[str]:
    return {
        issue.code.value
        for issue in [
            *preflight.issues,
            *(issue for case in preflight.cases for issue in case.issues),
            *(issue for image in preflight.images for issue in image.issues),
        ]
    }


def test_committed_p1_manifest_matches_deterministic_suite_source(tmp_path: Path) -> None:
    generated = tmp_path / "p1-packages-v1.json"

    write_manifest(generated)

    assert generated.read_bytes() == COMMITTED_MANIFEST.read_bytes()


def test_p1_suite_covers_the_required_package_and_lifecycle_matrix() -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)

    assert manifest.owner.value == "batch_workflow"
    assert len(manifest.cases) == 18
    assert {case.id for case in manifest.cases} == {
        "valid-2",
        "valid-5",
        "valid-25",
        "missing-image",
        "extra-unreferenced-image",
        "duplicate-application-id",
        "duplicate-image-reference",
        "unicode-filename-match",
        "ambiguous-filename-collision",
        "invalid-values",
        "corrupt-image",
        "over-case-limit",
        "over-image-limit",
        "correction-replacement",
        "mixed-lifecycle-25",
        "formula-safe-export",
        "cleanup-ready-only",
        "restart-partial",
    }
    lifecycle_cases = {case.id: case.expected_lifecycle for case in manifest.cases}
    assert lifecycle_cases["mixed-lifecycle-25"].maximum_concurrency == 2
    assert lifecycle_cases["formula-safe-export"].formula_safe_export is True
    assert lifecycle_cases["cleanup-ready-only"].expired_content_deleted is True
    assert lifecycle_cases["restart-partial"].replay_attempts == 0


def test_every_materialized_p1_artifact_matches_its_committed_hash(tmp_path: Path) -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)

    materialize_manifest(manifest, tmp_path)

    for case in manifest.cases:
        actual = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (tmp_path / case.id).iterdir()
        }
        assert actual == {artifact.filename: artifact.sha256 for artifact in case.artifacts}


def test_materialization_rejects_a_manifest_hash_mismatch(tmp_path: Path) -> None:
    manifest = build_manifest()
    case = manifest.cases[0]
    case.artifacts[0].sha256 = "0" * 64

    with pytest.raises(ValueError, match="generated artifact hash differs"):
        materialize_case(case, tmp_path)


@pytest.mark.parametrize("case_id", ["valid-2", "valid-5", "valid-25"])
def test_generated_csv_and_xlsx_packages_parse_to_identical_preflight(
    tmp_path: Path,
    case_id: str,
) -> None:
    manifest = build_manifest()
    case = next(candidate for candidate in manifest.cases if candidate.id == case_id)
    package = generate_package(case)

    async def run() -> None:
        results = []
        for format_ in (SpreadsheetFormat.CSV, SpreadsheetFormat.XLSX):
            async with prepare_batch_preflight(
                _upload(package.spreadsheets[format_], f"applications.{format_.value}"),
                [_upload(image.content, image.filename) for image in package.images],
                temp_dir=tmp_path / format_.value,
            ) as preflight:
                results.append(
                    (
                        preflight.issues,
                        preflight.ready_case_count,
                        preflight.correction_case_count,
                        tuple(case.row for case in preflight.cases),
                    )
                )
        assert results[0] == results[1]

    asyncio.run(run())


@pytest.mark.parametrize(
    "case_id",
    [
        "missing-image",
        "extra-unreferenced-image",
        "duplicate-application-id",
        "duplicate-image-reference",
        "unicode-filename-match",
        "ambiguous-filename-collision",
        "invalid-values",
        "corrupt-image",
        "over-case-limit",
        "over-image-limit",
        "correction-replacement",
        "mixed-lifecycle-25",
        "formula-safe-export",
        "cleanup-ready-only",
        "restart-partial",
    ],
)
def test_generated_p1_case_meets_its_preflight_expectation(
    tmp_path: Path,
    case_id: str,
) -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)
    case = next(candidate for candidate in manifest.cases if candidate.id == case_id)
    package = generate_package(case)
    expected = case.expected_preflight
    assert expected is not None

    async def run() -> None:
        format_, spreadsheet = next(iter(package.spreadsheets.items()))
        async with prepare_batch_preflight(
            _upload(spreadsheet, f"applications.{format_.value}"),
            [_upload(image.content, image.filename) for image in package.images],
            temp_dir=tmp_path / case_id,
        ) as preflight:
            assert {code.value for code in expected.issue_codes} <= _all_issue_codes(preflight)
            assert preflight.ready_case_count == expected.ready_case_count
            assert preflight.correction_case_count == expected.correction_case_count

    asyncio.run(run())
