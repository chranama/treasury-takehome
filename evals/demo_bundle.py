"""Generate the committed, reviewer-facing P0 and P1 demo bundle."""

import argparse
import csv
import hashlib
import tempfile
from io import StringIO
from pathlib import Path

from app.batches import BATCH_TEMPLATE_HEADERS, generate_csv_template, generate_xlsx_template
from app.config import PROJECT_ROOT
from evals.manifest import EvaluationManifestV2
from evals.renderer import ArtworkSpec, render_artwork
from evals.visual_suite import source_cases as visual_source_cases

DEFAULT_MANIFEST_OUTPUT = PROJECT_ROOT / "fixtures" / "reviewer-demo-v1.json"
DEFAULT_BUNDLE_DIRECTORY = PROJECT_ROOT / "frontend" / "public" / "demo"

_VISUAL_REVISION = "hosted-visual-v2"
_BATCH_REVISION = "p1-packages-v1"
_BRAND = "OLD TOM"
_CLASS_TYPE = "Kentucky Straight Bourbon Whiskey"
_ABV = "45"
_NET_CONTENTS = "750 mL"


def _csv_bytes(rows: tuple[tuple[str, ...], ...]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(BATCH_TEMPLATE_HEADERS)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _artifact(filename: str, media_type: str, content: bytes) -> dict[str, str]:
    return {"filename": filename, "media_type": media_type, "sha256": _sha256(content)}


def _visual_sources() -> dict[str, dict[str, object]]:
    return {case["id"]: case for case in visual_source_cases()}


def _render_visual(case: dict[str, object], filename: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="treasury-reviewer-demo-") as raw_directory:
        destination = Path(raw_directory) / filename
        render_artwork(ArtworkSpec.model_validate(case["artwork"]), destination)
        return destination.read_bytes()


def _p0_case(
    source: dict[str, object],
    *,
    case_id: str,
    scenario: str,
    directory: str,
    filename: str,
    instructions: list[str],
) -> tuple[dict[str, object], dict[str, bytes]]:
    image = _render_visual(source, filename)
    case = {
        "id": case_id,
        "purpose": source["purpose"],
        "families": ["reviewer_demo"],
        "layers": ["manifest_schema", "deterministic_rendering", "browser"],
        "expected_application": source["expected_application"],
        "expected_review": source["expected_review"],
        "reviewer_demo": {
            "generator": "reviewer-demo",
            "version": "1",
            "workflow": "p0_single_review",
            "scenario": scenario,
            "directory": directory,
            "source_revision": _VISUAL_REVISION,
            "source_case_ids": [source["id"]],
            "instructions": instructions,
        },
        "artifacts": [_artifact(filename, "image/png", image)],
    }
    return case, {f"{directory}/{filename}": image}


def build_bundle() -> tuple[EvaluationManifestV2, dict[str, bytes]]:
    """Build the manifest and every byte intended for ``frontend/public/demo``."""

    sources = _visual_sources()
    cases: list[dict[str, object]] = []
    files: dict[str, bytes] = {}

    p0_definitions = (
        (
            "p0-matching-label",
            "matching_label",
            "matching-label.png",
            "clear-composite",
            [
                "Enter OLD TOM, Kentucky Straight Bourbon Whiskey, 45 ABV, and 750 mL.",
                "Upload matching-label.png and select Review label.",
                "Expect All checks passed with five matching checks.",
            ],
        ),
        (
            "p0-material-mismatch",
            "material_mismatch",
            "material-net-mismatch.png",
            "material-net-mismatch",
            [
                "Enter OLD TOM, Kentucky Straight Bourbon Whiskey, 45 ABV, and 750 mL.",
                "Upload material-net-mismatch.png and select Review label.",
                "Expect Needs review because the artwork says 700 mL.",
            ],
        ),
        (
            "p0-unreadable-label",
            "unreadable_label",
            "unreadable-label.png",
            "degraded-unreadable",
            [
                "Enter OLD TOM, Kentucky Straight Bourbon Whiskey, 45 ABV, and 750 mL.",
                "Upload unreadable-label.png and select Review label.",
                "Expect Needs review rather than invented label values.",
            ],
        ),
    )
    for case_id, scenario, filename, source_id, instructions in p0_definitions:
        case, generated = _p0_case(
            sources[source_id],
            case_id=case_id,
            scenario=scenario,
            directory="p0",
            filename=filename,
            instructions=instructions,
        )
        cases.append(case)
        files.update(generated)

    csv_template = generate_csv_template()
    xlsx_template = generate_xlsx_template()
    cases.append(
        {
            "id": "blank-batch-templates",
            "purpose": "Provide both accepted blank spreadsheet formats for reviewer use.",
            "families": ["reviewer_demo"],
            "layers": ["manifest_schema", "browser"],
            "reviewer_demo": {
                "generator": "reviewer-demo",
                "version": "1",
                "workflow": "p1_batch_review",
                "scenario": "blank_templates",
                "directory": "templates",
                "source_revision": _BATCH_REVISION,
                "source_case_ids": [],
                "instructions": [
                    "Download either template and keep its column names unchanged.",
                    "Complete one row per application and select the referenced images separately.",
                ],
            },
            "artifacts": [
                _artifact("label-review-batch.csv", "text/csv", csv_template),
                _artifact(
                    "label-review-batch.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    xlsx_template,
                ),
            ],
        }
    )
    files["templates/label-review-batch.csv"] = csv_template
    files["templates/label-review-batch.xlsx"] = xlsx_template

    matching = _render_visual(sources["clear-composite"], "matching-label.png")
    mismatch = _render_visual(sources["material-net-mismatch"], "material-net-mismatch.png")
    valid_rows = (
        ("DEMO-PASS", "matching-label.png", _BRAND, _CLASS_TYPE, _ABV, _NET_CONTENTS),
        (
            "DEMO-MISMATCH",
            "material-net-mismatch.png",
            _BRAND,
            _CLASS_TYPE,
            _ABV,
            _NET_CONTENTS,
        ),
    )
    valid_csv = _csv_bytes(valid_rows)
    cases.append(
        {
            "id": "p1-valid-package",
            "purpose": (
                "Provide a two-case package that passes preflight and demonstrates independent "
                "outcomes."
            ),
            "families": ["reviewer_demo"],
            "layers": [
                "manifest_schema",
                "deterministic_rendering",
                "browser",
                "p1_preflight_batch",
            ],
            "expected_preflight": {
                "issue_codes": [],
                "ready_case_count": 2,
                "correction_case_count": 0,
            },
            "reviewer_demo": {
                "generator": "reviewer-demo",
                "version": "1",
                "workflow": "p1_batch_review",
                "scenario": "valid_batch",
                "directory": "p1/valid",
                "source_revision": _VISUAL_REVISION,
                "source_case_ids": ["clear-composite", "material-net-mismatch"],
                "instructions": [
                    "Select applications.csv as the spreadsheet and both PNG files as label "
                    "images.",
                    "Expect two ready cases and no corrections after Check batch.",
                    "When processed, expect DEMO-PASS to pass and DEMO-MISMATCH to need review "
                    "for 700 mL.",
                ],
            },
            "artifacts": [
                _artifact("applications.csv", "text/csv", valid_csv),
                _artifact("matching-label.png", "image/png", matching),
                _artifact("material-net-mismatch.png", "image/png", mismatch),
            ],
        }
    )
    files.update(
        {
            "p1/valid/applications.csv": valid_csv,
            "p1/valid/matching-label.png": matching,
            "p1/valid/material-net-mismatch.png": mismatch,
        }
    )

    mixed_rows = (
        ("DEMO-READY", "matching-label.png", _BRAND, _CLASS_TYPE, _ABV, _NET_CONTENTS),
        ("DEMO-FIX", "missing-label.png", _BRAND, _CLASS_TYPE, "101", _NET_CONTENTS),
    )
    mixed_csv = _csv_bytes(mixed_rows)
    cases.append(
        {
            "id": "p1-mixed-preflight",
            "purpose": (
                "Provide one ready row and one repairable row with an intentionally unreferenced "
                "replacement image."
            ),
            "families": ["reviewer_demo"],
            "layers": [
                "manifest_schema",
                "deterministic_rendering",
                "browser",
                "p1_preflight_batch",
            ],
            "expected_preflight": {
                "issue_codes": ["missing_image", "unreferenced_image", "invalid_abv"],
                "ready_case_count": 1,
                "correction_case_count": 1,
                "ready_after_correction": 2,
            },
            "reviewer_demo": {
                "generator": "reviewer-demo",
                "version": "1",
                "workflow": "p1_batch_review",
                "scenario": "mixed_preflight",
                "directory": "p1/mixed-errors",
                "source_revision": _VISUAL_REVISION,
                "source_case_ids": ["clear-composite"],
                "instructions": [
                    "Select applications.csv as the spreadsheet and both PNG files as label "
                    "images.",
                    "Expect one ready case, one case needing correction, and an unreferenced-image "
                    "warning.",
                    "Change DEMO-FIX ABV to 45 and replace its missing image with "
                    "replacement-label.png.",
                    "Expect two ready cases after both corrections.",
                ],
            },
            "artifacts": [
                _artifact("applications.csv", "text/csv", mixed_csv),
                _artifact("matching-label.png", "image/png", matching),
                _artifact("replacement-label.png", "image/png", matching),
            ],
        }
    )
    files.update(
        {
            "p1/mixed-errors/applications.csv": mixed_csv,
            "p1/mixed-errors/matching-label.png": matching,
            "p1/mixed-errors/replacement-label.png": matching,
        }
    )

    manifest = EvaluationManifestV2.model_validate(
        {
            "schema_version": 2,
            "revision": "reviewer-demo-v1",
            "owner": "demo_bundle",
            "purpose": (
                "Provide minimal, attributable P0 and P1 files that a reviewer can use without "
                "inventing application data or expected outcomes."
            ),
            "cases": cases,
        }
    )
    return manifest, files


def write_bundle(
    manifest_path: Path = DEFAULT_MANIFEST_OUTPUT,
    bundle_directory: Path = DEFAULT_BUNDLE_DIRECTORY,
) -> EvaluationManifestV2:
    manifest, files = build_bundle()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        manifest.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    for relative_path, content in files.items():
        destination = bundle_directory / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the reviewer-facing demo bundle.")
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_OUTPUT)
    parser.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIRECTORY)
    args = parser.parse_args()
    manifest = write_bundle(args.manifest_output, args.bundle_dir)
    artifact_count = sum(len(case.artifacts) for case in manifest.cases)
    print(f"Wrote {artifact_count} reviewer demo artifacts from {len(manifest.cases)} cases.")


if __name__ == "__main__":
    main()
