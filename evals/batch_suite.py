import argparse
import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from PIL import Image

from app.batches import BATCH_TEMPLATE_HEADERS, PreflightIssueCode, generate_xlsx_template
from app.batches.templates import normalize_xlsx_archive
from app.config import PROJECT_ROOT
from evals.manifest import (
    BatchPackageSpec,
    BatchPackageVariant,
    EvaluationCaseV2,
    EvaluationManifestV2,
    SpreadsheetFormat,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "fixtures" / "p1-packages-v1.json"
_FIXED_TIMESTAMP = datetime(2000, 1, 1, 0, 0, 0)
_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    filename: str
    content: bytes
    media_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class GeneratedBatchPackage:
    rows: tuple[tuple[str, ...], ...]
    spreadsheets: dict[SpreadsheetFormat, bytes]
    images: tuple[GeneratedImage, ...]


def _csv_bytes(rows: tuple[tuple[str, ...], ...]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(BATCH_TEMPLATE_HEADERS)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _xlsx_bytes(rows: tuple[tuple[str, ...], ...]) -> bytes:
    workbook = load_workbook(BytesIO(generate_xlsx_template()))
    workbook.properties.creator = "Label Review"
    workbook.properties.lastModifiedBy = "Label Review"
    workbook.properties.created = _FIXED_TIMESTAMP
    workbook.properties.modified = _FIXED_TIMESTAMP
    for row in rows:
        workbook["Batch"].append(row)
    raw = BytesIO()
    workbook.save(raw)
    workbook.close()
    return normalize_xlsx_archive(raw.getvalue())


def _png_bytes(color: str = "navy") -> bytes:
    image = Image.new("RGB", (40, 24), color=color)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def _valid_rows(count: int) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            f"APP-{index:02d}",
            f"label-{index:02d}.png",
            "Treasury Reserve",
            "Kentucky Straight Bourbon Whiskey",
            "45%",
            "0.75 L",
        )
        for index in range(1, count + 1)
    )


def _valid_images(count: int) -> tuple[GeneratedImage, ...]:
    return tuple(
        GeneratedImage(filename=f"label-{index:02d}.png", content=_png_bytes())
        for index in range(1, count + 1)
    )


def _source_inputs(
    variant: BatchPackageVariant,
    case_count: int,
) -> tuple[tuple[tuple[str, ...], ...], tuple[GeneratedImage, ...]]:
    base = ("APP-1", "label.png", "Brand", "Bourbon", "45", "750 mL")
    if variant in {
        BatchPackageVariant.VALID,
        BatchPackageVariant.MIXED_LIFECYCLE,
        BatchPackageVariant.RESTART_PARTIAL,
    }:
        return _valid_rows(case_count), _valid_images(case_count)
    if variant == BatchPackageVariant.MISSING_IMAGE:
        return (base,), ()
    if variant == BatchPackageVariant.EXTRA_IMAGE:
        return (base,), (
            GeneratedImage("label.png", _png_bytes()),
            GeneratedImage("unused.png", _png_bytes("green")),
        )
    if variant == BatchPackageVariant.DUPLICATE_APPLICATION_ID:
        return (
            base,
            (" app-1 ", "two.png", "Brand", "Bourbon", "45", "750 mL"),
        ), (
            GeneratedImage("label.png", _png_bytes()),
            GeneratedImage("two.png", _png_bytes("blue")),
        )
    if variant == BatchPackageVariant.DUPLICATE_IMAGE_REFERENCE:
        return (
            base,
            ("APP-2", "LABEL.PNG", "Brand", "Bourbon", "45", "750 mL"),
        ), (GeneratedImage("label.png", _png_bytes()),)
    if variant == BatchPackageVariant.UNICODE_FILENAME:
        return (("APP-1", "cafe\u0301.png", "Brand", "Bourbon", "45", "750 mL"),), (
            GeneratedImage(" CAFÉ.PNG ", _png_bytes()),
        )
    if variant == BatchPackageVariant.AMBIGUOUS_FILENAME:
        return (base,), (
            GeneratedImage("Label.PNG", _png_bytes()),
            GeneratedImage("label.png", _png_bytes("blue")),
        )
    if variant == BatchPackageVariant.INVALID_VALUES:
        return (("APP-1", "label.png", "Brand", "Bourbon", "101", "750 oz"),), (
            GeneratedImage("label.png", _png_bytes()),
        )
    if variant == BatchPackageVariant.CORRUPT_IMAGE:
        return (base,), (GeneratedImage("label.png", b"not an image", "application/octet-stream"),)
    if variant == BatchPackageVariant.OVER_CASE_LIMIT:
        return _valid_rows(26), ()
    if variant == BatchPackageVariant.OVER_IMAGE_LIMIT:
        return (base,), tuple(
            GeneratedImage(f"image-{index:02d}.png", _png_bytes()) for index in range(1, 27)
        )
    if variant == BatchPackageVariant.CORRECTION_REPLACEMENT:
        return (
            ("APP-1", "one.png", "Brand", "Bourbon", "45", "750 mL"),
            ("APP-2", "missing.png", "Brand", "Bourbon", "101", "750 mL"),
        ), (GeneratedImage("one.png", _png_bytes("red")),)
    if variant == BatchPackageVariant.FORMULA_EXPORT:
        return (("APP-FORMULA", "label.png", "Brand", "Bourbon", "45", "750 mL"),), (
            GeneratedImage("label.png", _png_bytes()),
        )
    if variant == BatchPackageVariant.CLEANUP_READY_ONLY:
        return (
            ("APP-1", "one.png", "Brand", "Bourbon", "45", "750 mL"),
            ("APP-2", "two.png", "Brand", "Bourbon", "101", "750 mL"),
        ), (
            GeneratedImage("one.png", _png_bytes("red")),
            GeneratedImage("two.png", _png_bytes("blue")),
        )
    raise ValueError(f"unsupported P1 package variant: {variant}")


def _generate_package(spec: BatchPackageSpec) -> GeneratedBatchPackage:
    rows, images = _source_inputs(spec.variant, spec.case_count)
    if [row[1] for row in rows] != spec.row_image_filenames:
        raise ValueError("generated row filenames differ from the manifest")
    if [image.filename for image in images] != spec.upload_image_filenames:
        raise ValueError("generated upload filenames differ from the manifest")
    spreadsheets = {
        format_: _csv_bytes(rows) if format_ == SpreadsheetFormat.CSV else _xlsx_bytes(rows)
        for format_ in spec.formats
    }
    return GeneratedBatchPackage(rows=rows, spreadsheets=spreadsheets, images=images)


def generate_package(case: EvaluationCaseV2) -> GeneratedBatchPackage:
    spec = case.batch_package
    if spec is None:
        raise ValueError("P1 package generation requires batch package metadata")
    return _generate_package(spec)


def _case(
    case_id: str,
    purpose: str,
    *,
    variant: BatchPackageVariant,
    case_count: int,
    formats: tuple[SpreadsheetFormat, ...] = (SpreadsheetFormat.CSV,),
    issue_codes: tuple[PreflightIssueCode, ...] = (),
    ready: int,
    corrections: int,
    ready_after_correction: int | None = None,
    lifecycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows, images = _source_inputs(variant, case_count)
    layers = ["manifest_schema", "p1_preflight_batch"]
    if lifecycle is not None or ready_after_correction is not None:
        layers.append("fake_adapter_api")
    return {
        "id": case_id,
        "purpose": purpose,
        "families": ["p1_batch_package"],
        "layers": layers,
        "batch_package": {
            "generator": "p1-package",
            "version": "1",
            "variant": variant,
            "case_count": case_count,
            "formats": list(formats),
            "row_image_filenames": [row[1] for row in rows],
            "upload_image_filenames": [image.filename for image in images],
        },
        "expected_preflight": {
            "issue_codes": list(issue_codes),
            "ready_case_count": ready,
            "correction_case_count": corrections,
            "ready_after_correction": ready_after_correction,
        },
        "expected_lifecycle": lifecycle,
        "artifacts": [],
    }


def source_cases() -> list[dict[str, Any]]:
    both = (SpreadsheetFormat.CSV, SpreadsheetFormat.XLSX)
    return [
        _case(
            "valid-2",
            "Round-trip a two-case package in CSV and XLSX.",
            variant=BatchPackageVariant.VALID,
            case_count=2,
            formats=both,
            ready=2,
            corrections=0,
        ),
        _case(
            "valid-5",
            "Round-trip a five-case package in CSV and XLSX.",
            variant=BatchPackageVariant.VALID,
            case_count=5,
            formats=both,
            ready=5,
            corrections=0,
        ),
        _case(
            "valid-25",
            "Exercise the maximum accepted package in CSV and XLSX.",
            variant=BatchPackageVariant.VALID,
            case_count=25,
            formats=both,
            ready=25,
            corrections=0,
        ),
        _case(
            "missing-image",
            "Report a referenced image that was not selected.",
            variant=BatchPackageVariant.MISSING_IMAGE,
            case_count=1,
            issue_codes=(PreflightIssueCode.MISSING_IMAGE,),
            ready=0,
            corrections=1,
        ),
        _case(
            "extra-unreferenced-image",
            "Warn about a selected image that no row references.",
            variant=BatchPackageVariant.EXTRA_IMAGE,
            case_count=1,
            issue_codes=(PreflightIssueCode.UNREFERENCED_IMAGE,),
            ready=1,
            corrections=0,
        ),
        _case(
            "duplicate-application-id",
            "Reject duplicate normalized application identifiers.",
            variant=BatchPackageVariant.DUPLICATE_APPLICATION_ID,
            case_count=2,
            issue_codes=(PreflightIssueCode.DUPLICATE_APPLICATION_ID,),
            ready=0,
            corrections=2,
        ),
        _case(
            "duplicate-image-reference",
            "Reject two rows that normalize to one image reference.",
            variant=BatchPackageVariant.DUPLICATE_IMAGE_REFERENCE,
            case_count=2,
            issue_codes=(PreflightIssueCode.DUPLICATE_IMAGE_FILENAME,),
            ready=0,
            corrections=2,
        ),
        _case(
            "unicode-filename-match",
            "Match Unicode-normalized and case-insensitive filenames.",
            variant=BatchPackageVariant.UNICODE_FILENAME,
            case_count=1,
            ready=1,
            corrections=0,
        ),
        _case(
            "ambiguous-filename-collision",
            "Reject multiple uploads with one normalized filename.",
            variant=BatchPackageVariant.AMBIGUOUS_FILENAME,
            case_count=1,
            issue_codes=(PreflightIssueCode.AMBIGUOUS_IMAGE_FILENAME,),
            ready=0,
            corrections=1,
        ),
        _case(
            "invalid-values",
            "Report invalid ABV and net-contents cells together.",
            variant=BatchPackageVariant.INVALID_VALUES,
            case_count=1,
            issue_codes=(PreflightIssueCode.INVALID_ABV, PreflightIssueCode.INVALID_NET_CONTENTS),
            ready=0,
            corrections=1,
        ),
        _case(
            "corrupt-image",
            "Reject corrupt image bytes before extraction.",
            variant=BatchPackageVariant.CORRUPT_IMAGE,
            case_count=1,
            issue_codes=(PreflightIssueCode.UNSUPPORTED_IMAGE,),
            ready=0,
            corrections=1,
        ),
        _case(
            "over-case-limit",
            "Reject more than 25 nonblank application rows.",
            variant=BatchPackageVariant.OVER_CASE_LIMIT,
            case_count=26,
            issue_codes=(PreflightIssueCode.TOO_MANY_CASES,),
            ready=0,
            corrections=25,
        ),
        _case(
            "over-image-limit",
            "Reject more than 25 selected images.",
            variant=BatchPackageVariant.OVER_IMAGE_LIMIT,
            case_count=1,
            issue_codes=(PreflightIssueCode.TOO_MANY_IMAGES,),
            ready=0,
            corrections=0,
        ),
        _case(
            "correction-replacement",
            "Correct an invalid value and replace a missing image without rebuilding the batch.",
            variant=BatchPackageVariant.CORRECTION_REPLACEMENT,
            case_count=2,
            issue_codes=(PreflightIssueCode.MISSING_IMAGE, PreflightIssueCode.INVALID_ABV),
            ready=1,
            corrections=1,
            ready_after_correction=2,
        ),
        _case(
            "mixed-lifecycle-25",
            "Complete a 25-case fixed-response batch with independent mismatches, unreadable "
            "evidence, and failures.",
            variant=BatchPackageVariant.MIXED_LIFECYCLE,
            case_count=25,
            ready=25,
            corrections=0,
            lifecycle={
                "selection": "all_cases",
                "completed": 23,
                "failed": 2,
                "outcomes": ["all_checks_passed", "needs_review"],
                "maximum_concurrency": 2,
                "provider_attempts": 25,
                "processed_images_deleted": True,
            },
        ),
        _case(
            "formula-safe-export",
            "Neutralize formula-prefix values in terminal CSV output.",
            variant=BatchPackageVariant.FORMULA_EXPORT,
            case_count=1,
            ready=1,
            corrections=0,
            lifecycle={
                "selection": "all_cases",
                "completed": 1,
                "formula_safe_export": True,
                "processed_images_deleted": True,
            },
        ),
        _case(
            "cleanup-ready-only",
            "Delete processed images immediately and unselected content at expiry.",
            variant=BatchPackageVariant.CLEANUP_READY_ONLY,
            case_count=2,
            issue_codes=(PreflightIssueCode.INVALID_ABV,),
            ready=1,
            corrections=1,
            lifecycle={
                "selection": "ready_cases_only",
                "completed": 1,
                "not_selected": 1,
                "provider_attempts": 1,
                "processed_images_deleted": True,
                "expired_content_deleted": True,
            },
        ),
        _case(
            "restart-partial",
            "Preserve one completed case and interrupt uncertain work without replay after "
            "restart.",
            variant=BatchPackageVariant.RESTART_PARTIAL,
            case_count=3,
            ready=3,
            corrections=0,
            lifecycle={
                "selection": "all_cases",
                "completed": 1,
                "interrupted": 2,
                "provider_attempts": 3,
                "replay_attempts": 0,
                "processed_images_deleted": True,
            },
        ),
    ]


def _artifact_rows(package: GeneratedBatchPackage) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    for format_, content in package.spreadsheets.items():
        artifacts.append(
            {
                "filename": f"applications.{format_.value}",
                "media_type": "text/csv" if format_ == SpreadsheetFormat.CSV else _XLSX_MEDIA_TYPE,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    for index, image in enumerate(package.images, start=1):
        suffix = ".png" if image.media_type == "image/png" else ".bin"
        artifacts.append(
            {
                "filename": f"image-{index:02d}{suffix}",
                "media_type": image.media_type,
                "sha256": hashlib.sha256(image.content).hexdigest(),
            }
        )
    return artifacts


def build_manifest() -> EvaluationManifestV2:
    cases = source_cases()
    for raw_case in cases:
        spec = BatchPackageSpec.model_validate(raw_case["batch_package"])
        raw_case["artifacts"] = _artifact_rows(_generate_package(spec))
    return EvaluationManifestV2.model_validate(
        {
            "schema_version": 2,
            "revision": "p1-packages-v1",
            "owner": "batch_workflow",
            "purpose": (
                "Generate deterministic P1 packages and map every offline batch acceptance path "
                "to expected preflight and lifecycle evidence."
            ),
            "cases": cases,
        }
    )


def write_manifest(path: Path, manifest: EvaluationManifestV2 | None = None) -> None:
    selected = manifest or build_manifest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        selected.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )


def materialize_case(case: EvaluationCaseV2, directory: Path) -> None:
    package = generate_package(case)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = iter(case.artifacts)
    for _, content in package.spreadsheets.items():
        artifact = next(artifacts)
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ValueError(f"generated artifact hash differs for {case.id}/{artifact.filename}")
        (directory / artifact.filename).write_bytes(content)
    for image in package.images:
        artifact = next(artifacts)
        if hashlib.sha256(image.content).hexdigest() != artifact.sha256:
            raise ValueError(f"generated artifact hash differs for {case.id}/{artifact.filename}")
        (directory / artifact.filename).write_bytes(image.content)


def materialize_manifest(manifest: EvaluationManifestV2, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for case in manifest.cases:
        materialize_case(case, directory / case.id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the deterministic P1 package suite.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--materialize-dir", type=Path, default=None)
    args = parser.parse_args()
    manifest = build_manifest()
    write_manifest(args.output, manifest)
    if args.materialize_dir is not None:
        materialize_manifest(manifest, args.materialize_dir)
    print(f"Wrote {len(manifest.cases)} P1 package cases to {args.output}.")


if __name__ == "__main__":
    main()
