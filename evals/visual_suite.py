import argparse
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.comparison import GOVERNMENT_WARNING_TEXT
from app.config import PROJECT_ROOT
from evals.manifest import EvaluationManifestV2
from evals.renderer import (
    FONT_IDENTITY,
    RENDERER_ID,
    RENDERER_VERSION,
    ArtworkSpec,
    render_artwork,
    render_case,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "fixtures" / "hosted-visual-v2.json"
ALTERED_WARNING_TEXT = GOVERNMENT_WARNING_TEXT.replace(
    "may cause health problems",
    "might cause health problems",
)

_MATCHING_CHECKS = {
    "brand_name": "match",
    "class_type": "match",
    "alcohol_content": "match",
    "net_contents": "match",
    "government_warning": "match",
}


def _base_artwork() -> dict[str, Any]:
    return {
        "canvas": {"width": 1_600, "height": 1_200},
        "layout": {
            "kind": "front_back_composite",
            "outer_margin": 64,
            "panel_gap": 48,
            "back_panel_rotation_degrees": 0,
        },
        "typography": {
            "brand_size": 72,
            "class_type_size": 36,
            "detail_size": 32,
            "warning_heading_size": 26,
            "warning_body_size": 21,
            "warning_line_spacing": 6,
            "brand_weight": "bold",
            "warning_heading_weight": "bold",
            "warning_body_weight": "regular",
        },
        "brand_names": ["OLD TOM"],
        "class_types": ["Kentucky Straight Bourbon Whiskey"],
        "alcohol_contents": ["45% Alc./Vol. (90 Proof)"],
        "net_contents": ["750 mL"],
        "government_warning": GOVERNMENT_WARNING_TEXT,
        "degradation": {
            "contrast": 1,
            "glare_box": None,
            "glare_opacity": 0,
            "obstruction_box": None,
            "blur_radius": 0,
            "rotation_degrees": 0,
            "crop": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        },
    }


def _expected_application() -> dict[str, Any]:
    return {
        "brand_name": "OLD TOM",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "abv": "45",
        "net_contents": {"value": "750", "unit": "mL"},
    }


def _readable_field(candidates: str = "exact") -> dict[str, Any]:
    return {
        "candidates": candidates,
        "visibility": ["visible"],
        "readability": ["readable"],
    }


def _uncertain_field() -> dict[str, Any]:
    return {
        "candidates": "empty",
        "visibility": ["uncertain"],
        "readability": ["unreadable", "uncertain"],
    }


def _exact_warning() -> dict[str, Any]:
    return {
        "text": "exact",
        "heading_text": "exact",
        "heading_weight": ["bold"],
        "body_weight": ["not_bold"],
        "visibility": ["visible"],
        "readability": ["readable"],
    }


def _uncertain_warning() -> dict[str, Any]:
    return {
        "text": "any",
        "heading_text": "any",
        "heading_weight": ["uncertain"],
        "body_weight": ["uncertain"],
        "visibility": ["uncertain"],
        "readability": ["partially_readable", "unreadable", "uncertain"],
    }


def _partially_unreadable_warning() -> dict[str, Any]:
    return {
        "text": "any",
        "heading_text": "any",
        "heading_weight": ["bold", "uncertain"],
        "body_weight": ["not_bold", "uncertain"],
        "visibility": ["visible", "uncertain"],
        "readability": ["partially_readable", "unreadable", "uncertain"],
    }


def _visible_text(artwork: dict[str, Any]) -> dict[str, Any]:
    warning = artwork["government_warning"]
    return {
        "brand_name": artwork["brand_names"],
        "class_type": artwork["class_types"],
        "alcohol_content": artwork["alcohol_contents"],
        "net_contents": artwork["net_contents"],
        "government_warning": warning,
        "warning_heading": warning.split(":", 1)[0] if warning is not None else None,
    }


def _case(
    case_id: str,
    purpose: str,
    *,
    artwork_changes: dict[str, Any] | None = None,
    application_changes: dict[str, Any] | None = None,
    observation_changes: dict[str, Any] | None = None,
    check_changes: dict[str, str | list[str]] | None = None,
    uncertainty: str = "forbidden",
) -> dict[str, Any]:
    artwork = _base_artwork()
    for group, value in (artwork_changes or {}).items():
        if isinstance(value, dict) and isinstance(artwork.get(group), dict):
            artwork[group].update(value)
        else:
            artwork[group] = value

    expected_application = _expected_application()
    expected_application.update(application_changes or {})
    observations = {
        "brand_name": _readable_field(),
        "class_type": _readable_field(),
        "alcohol_content": _readable_field(),
        "net_contents": _readable_field(),
        "government_warning": _exact_warning(),
    }
    observations.update(observation_changes or {})
    checks = deepcopy(_MATCHING_CHECKS)
    checks.update(check_changes or {})
    outcome = (
        "all_checks_passed"
        if all(status == "match" for status in checks.values())
        else "needs_review"
    )
    return {
        "id": case_id,
        "purpose": purpose,
        "families": ["hosted_model_visual"],
        "layers": [
            "manifest_schema",
            "deterministic_rendering",
            "manual_visual_inspection",
            "live_provider",
        ],
        "renderer": {
            "id": RENDERER_ID,
            "version": RENDERER_VERSION,
            "font_identity": FONT_IDENTITY,
            "seed": None,
        },
        "artwork": artwork,
        "expected_visible_text": _visible_text(artwork),
        "expected_application": expected_application,
        "required_observations": observations,
        "expected_review": {"outcome": outcome, "checks": checks},
        "uncertainty": uncertainty,
        "artifacts": [],
    }


def source_cases() -> list[dict[str, Any]]:
    missing_warning = {
        "text": "absent",
        "heading_text": "absent",
        "heading_weight": ["uncertain"],
        "body_weight": ["uncertain"],
        "visibility": ["not_visible"],
        "readability": ["unreadable", "uncertain"],
    }
    return [
        _case("clear-composite", "Establish the clear front/back five-check baseline."),
        _case(
            "clear-single-panel",
            "Preserve every visible field in a single-panel layout.",
            artwork_changes={"layout": {"kind": "single_panel"}},
        ),
        _case(
            "brand-format-variation",
            "Preserve a case and typographic-apostrophe brand variation for deterministic "
            "normalization.",
            artwork_changes={"brand_names": ["Old Tom’s Distillery"]},
            application_changes={"brand_name": "OLD TOM'S DISTILLERY"},
        ),
        _case(
            "material-brand-difference",
            "Preserve a materially different visible brand without anchoring to expected data.",
            artwork_changes={"brand_names": ["OLD FOX DISTILLERY"]},
            check_changes={"brand_name": "needs_review"},
        ),
        _case(
            "material-class-difference",
            "Preserve a materially different visible class or type.",
            artwork_changes={"class_types": ["Kentucky Straight Rye Whiskey"]},
            check_changes={"class_type": "needs_review"},
        ),
        _case(
            "proof-only",
            "Extract proof-only artwork and leave conversion to deterministic comparison.",
            artwork_changes={"alcohol_contents": ["90 Proof"]},
        ),
        _case(
            "conflicting-alcohol",
            "Return both conflicting alcohol statements without silently choosing one.",
            artwork_changes={"alcohol_contents": ["45% Alc./Vol.", "80 Proof"]},
            check_changes={"alcohol_content": "needs_review"},
        ),
        _case(
            "equivalent-net-contents",
            "Extract 0.75 L for deterministic equivalence with 750 mL.",
            artwork_changes={"net_contents": ["0.75 L"]},
        ),
        _case(
            "material-net-mismatch",
            "Preserve a material visible quantity difference.",
            artwork_changes={"net_contents": ["700 mL"]},
            check_changes={"net_contents": "mismatch"},
        ),
        _case(
            "missing-warning",
            "Distinguish a warning that is not visible in the submitted artwork from unreadable "
            "artwork.",
            artwork_changes={"government_warning": None},
            observation_changes={
                "government_warning": missing_warning | {"visibility": ["not_visible", "uncertain"]}
            },
            check_changes={"government_warning": ["mismatch", "needs_review"]},
            uncertainty="allowed",
        ),
        _case(
            "altered-warning",
            "Transcribe one altered warning phrase exactly enough for deterministic rejection.",
            artwork_changes={"government_warning": ALTERED_WARNING_TEXT},
            check_changes={"government_warning": "mismatch"},
        ),
        _case(
            "incorrect-warning-weight",
            "Observe a visibly non-bold warning heading rather than inferring compliant styling.",
            artwork_changes={"typography": {"warning_heading_weight": "regular"}},
            observation_changes={
                "government_warning": _exact_warning() | {"heading_weight": ["not_bold"]}
            },
            check_changes={"government_warning": "mismatch"},
        ),
        _case(
            "ambiguous-brand-and-quantity",
            "Return all plausible brand and quantity candidates in an intentionally ambiguous "
            "label.",
            artwork_changes={
                "brand_names": ["OLD TOM DISTILLERY", "OLD TOM RESERVE"],
                "net_contents": ["750 mL", "700 mL"],
            },
            observation_changes={
                "brand_name": _readable_field("contains_all"),
                "net_contents": _readable_field("contains_all"),
            },
            check_changes={"brand_name": "needs_review", "net_contents": "needs_review"},
        ),
        _case(
            "rotated-back-panel",
            "Extract the complete warning from an independently rotated back panel.",
            artwork_changes={"layout": {"back_panel_rotation_degrees": -7}},
        ),
        _case(
            "small-warning-threshold",
            "Transcribe the smallest supported warning typography when it remains readable.",
            artwork_changes={"typography": {"warning_heading_size": 14, "warning_body_size": 12}},
        ),
        _case(
            "obscured-warning",
            "Report a partially obscured warning conservatively while preserving readable "
            "front-panel fields.",
            artwork_changes={
                "degradation": {
                    "obstruction_box": {
                        "left": 0.46,
                        "top": 0.16,
                        "right": 0.96,
                        "bottom": 0.30,
                    },
                }
            },
            observation_changes={"government_warning": _partially_unreadable_warning()},
            check_changes={"government_warning": "needs_review"},
            uncertainty="required",
        ),
        _case(
            "degraded-unreadable",
            "Return uncertainty without fabricated candidates under combined degradation.",
            artwork_changes={
                "degradation": {
                    "contrast": 0.35,
                    "glare_box": {"left": 0.08, "top": 0.08, "right": 0.94, "bottom": 0.55},
                    "glare_opacity": 0.8,
                    "obstruction_box": {
                        "left": 0.12,
                        "top": 0.62,
                        "right": 0.88,
                        "bottom": 0.82,
                    },
                    "blur_radius": 9,
                    "rotation_degrees": 5,
                    "crop": {"left": 0.12, "top": 0.08, "right": 0.12, "bottom": 0.08},
                }
            },
            observation_changes={
                "brand_name": _uncertain_field(),
                "class_type": _uncertain_field(),
                "alcohol_content": _uncertain_field(),
                "net_contents": _uncertain_field(),
                "government_warning": _uncertain_warning(),
            },
            check_changes={name: "needs_review" for name in _MATCHING_CHECKS},
            uncertainty="required",
        ),
        _case(
            "near-dimension-bound",
            "Measure a wide composite near the provisional maximum-side boundary.",
            artwork_changes={
                "canvas": {"width": 5_800, "height": 1_200},
                "typography": {
                    "brand_size": 110,
                    "class_type_size": 64,
                    "detail_size": 60,
                    "warning_heading_size": 44,
                    "warning_body_size": 32,
                },
            },
        ),
    ]


def build_manifest() -> EvaluationManifestV2:
    cases = source_cases()
    with tempfile.TemporaryDirectory(prefix="treasury-visual-suite-") as raw_directory:
        directory = Path(raw_directory)
        for case in cases:
            filename = f"{case['id']}.png"
            rendered = render_artwork(
                ArtworkSpec.model_validate(case["artwork"]),
                directory / filename,
            )
            case["artifacts"] = [
                {"filename": filename, "media_type": "image/png", "sha256": rendered.sha256}
            ]
    return EvaluationManifestV2.model_validate(
        {
            "schema_version": 2,
            "revision": "hosted-visual-v2",
            "owner": "hosted_extraction",
            "purpose": (
                "Evaluate visible label extraction and conservative uncertainty behavior across "
                "the required synthetic P0 visual matrix."
            ),
            "cases": cases,
        }
    )


def write_manifest(path: Path, manifest: EvaluationManifestV2 | None = None) -> None:
    selected = manifest or build_manifest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        selected.model_dump_json(
            indent=2,
            exclude={
                "cases": {
                    "__all__": {
                        "batch_package",
                        "expected_preflight",
                        "expected_lifecycle",
                        "reviewer_demo",
                    }
                }
            },
        )
        + "\n",
        encoding="utf-8",
    )


def render_manifest_artifacts(manifest: EvaluationManifestV2, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for case in manifest.cases:
        render_case(case, directory / case.artifacts[0].filename)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the deterministic hosted visual suite.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--render-dir",
        type=Path,
        default=None,
        help="Optionally render every hash-verified PNG for manual inspection.",
    )
    args = parser.parse_args()
    manifest = build_manifest()
    write_manifest(args.output, manifest)
    if args.render_dir is not None:
        render_manifest_artifacts(manifest, args.render_dir)
    print(f"Wrote {len(manifest.cases)} hosted visual cases to {args.output}.")


if __name__ == "__main__":
    main()
