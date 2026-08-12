import copy
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageFont
from pydantic import ValidationError

from app.comparison import GOVERNMENT_WARNING_TEXT
from evals.manifest import EvaluationManifestV2
from evals.renderer import (
    FONT_IDENTITY,
    RENDERER_ID,
    RENDERER_VERSION,
    ArtworkSpec,
    render_artwork,
    render_case,
)


def artwork_payload() -> dict[str, object]:
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
        "brand_names": ["Treasury Reserve"],
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


def manifest_payload(sha256: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "revision": "renderer-integration-v2",
        "owner": "hosted_extraction",
        "purpose": "Exercise the deterministic renderer contract.",
        "cases": [
            {
                "id": "clear-composite",
                "purpose": "Render a clear composite through the manifest boundary.",
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
                "artwork": artwork_payload(),
                "expected_visible_text": {
                    "brand_name": ["Treasury Reserve"],
                    "class_type": ["Kentucky Straight Bourbon Whiskey"],
                    "alcohol_content": ["45% Alc./Vol. (90 Proof)"],
                    "net_contents": ["750 mL"],
                    "government_warning": GOVERNMENT_WARNING_TEXT,
                    "warning_heading": "GOVERNMENT WARNING",
                },
                "expected_application": {
                    "brand_name": "Treasury Reserve",
                    "class_type": "Kentucky Straight Bourbon Whiskey",
                    "abv": "45",
                    "net_contents": {"value": "750", "unit": "mL"},
                },
                "required_observations": {
                    field: {
                        "candidates": "exact",
                        "visibility": ["visible"],
                        "readability": ["readable"],
                    }
                    for field in (
                        "brand_name",
                        "class_type",
                        "alcohol_content",
                        "net_contents",
                    )
                }
                | {
                    "government_warning": {
                        "text": "exact",
                        "heading_text": "exact",
                        "heading_weight": ["bold"],
                        "body_weight": ["not_bold"],
                        "visibility": ["visible"],
                        "readability": ["readable"],
                    }
                },
                "expected_review": {
                    "outcome": "all_checks_passed",
                    "checks": {
                        "brand_name": "match",
                        "class_type": "match",
                        "alcohol_content": "match",
                        "net_contents": "match",
                        "government_warning": "match",
                    },
                },
                "uncertainty": "forbidden",
                "artifacts": [
                    {
                        "filename": "clear-composite.png",
                        "media_type": "image/png",
                        "sha256": sha256,
                    }
                ],
            }
        ],
    }


def test_renderer_uses_recorded_embedded_font_identity() -> None:
    assert ImageFont.load_default(size=24).getname() == ("Aileron", "Regular")
    assert FONT_IDENTITY == "pillow-embedded-aileron-regular"


def test_identical_artwork_reproduces_identical_png_and_hash(tmp_path: Path) -> None:
    artwork = ArtworkSpec.model_validate(artwork_payload())

    first = render_artwork(artwork, tmp_path / "first.png")
    second = render_artwork(artwork, tmp_path / "second.png")

    assert first.sha256 == second.sha256
    assert first.image.path.read_bytes() == second.image.path.read_bytes()
    assert first.image.width == second.image.width == 1_600
    assert first.image.height == second.image.height == 1_200
    assert first.image.byte_count == len(first.image.path.read_bytes())
    with Image.open(first.image.path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.info == {}


def test_visual_controls_produce_distinct_inspectable_artifacts(tmp_path: Path) -> None:
    variants: dict[str, dict[str, object]] = {"clear-composite": artwork_payload()}

    single = artwork_payload()
    single["layout"]["kind"] = "single_panel"  # type: ignore[index]
    variants["single-panel"] = single

    typography = artwork_payload()
    typography["typography"].update(  # type: ignore[union-attr]
        {
            "brand_size": 54,
            "warning_heading_size": 18,
            "warning_body_size": 14,
            "warning_heading_weight": "regular",
            "warning_body_weight": "bold",
        }
    )
    variants["typography"] = typography

    ambiguity = artwork_payload()
    ambiguity["brand_names"] = ["Treasury Reserve", "Treasury Select"]
    ambiguity["net_contents"] = ["750 mL", "700 mL"]
    variants["ambiguity"] = ambiguity

    rotated_panel = artwork_payload()
    rotated_panel["layout"]["back_panel_rotation_degrees"] = -7  # type: ignore[index]
    variants["rotated-panel"] = rotated_panel

    degraded = artwork_payload()
    degraded["degradation"].update(  # type: ignore[union-attr]
        {
            "contrast": 0.55,
            "glare_box": {"left": 0.55, "top": 0.12, "right": 0.9, "bottom": 0.45},
            "glare_opacity": 0.65,
            "obstruction_box": {
                "left": 0.12,
                "top": 0.7,
                "right": 0.32,
                "bottom": 0.79,
            },
            "blur_radius": 2.5,
        }
    )
    variants["degraded"] = degraded

    rotated_cropped = artwork_payload()
    rotated_cropped["degradation"].update(  # type: ignore[union-attr]
        {
            "rotation_degrees": 8,
            "crop": {"left": 0.08, "top": 0.04, "right": 0.12, "bottom": 0.06},
        }
    )
    variants["rotated-cropped"] = rotated_cropped

    artifacts = {
        name: render_artwork(ArtworkSpec.model_validate(payload), tmp_path / f"{name}.png")
        for name, payload in variants.items()
    }

    assert len({artifact.sha256 for artifact in artifacts.values()}) == len(variants)
    assert artifacts["rotated-cropped"].image.width == 1_280
    assert artifacts["rotated-cropped"].image.height == 1_080
    with (
        Image.open(artifacts["clear-composite"].image.path) as baseline,
        Image.open(artifacts["degraded"].image.path) as degraded_image,
    ):
        difference = ImageChops.difference(baseline, degraded_image)
        assert difference.getbbox() is not None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("canvas", "width", 700), "greater than or equal to 800"),
        (("layout", "back_panel_rotation_degrees", 13), "less than or equal to 12"),
        (("degradation", "blur_radius", 13), "less than or equal to 12"),
        (("degradation", "glare_opacity", 0.5), "glare requires both"),
    ],
)
def test_renderer_rejects_out_of_contract_controls(
    mutation: tuple[str, str, object],
    message: str,
) -> None:
    payload = artwork_payload()
    group, field, value = mutation
    payload[group][field] = value  # type: ignore[index]

    with pytest.raises(ValidationError, match=message):
        ArtworkSpec.model_validate(payload)


def test_renderer_rejects_duplicate_ambiguity_candidates() -> None:
    payload = artwork_payload()
    payload["brand_names"] = ["Treasury Reserve", "Treasury Reserve"]

    with pytest.raises(ValidationError, match="must be unique"):
        ArtworkSpec.model_validate(payload)


def test_renderer_rejects_warning_without_heading_separator() -> None:
    payload = artwork_payload()
    payload["government_warning"] = "GOVERNMENT WARNING without a colon"

    with pytest.raises(ValidationError, match="heading separator"):
        ArtworkSpec.model_validate(payload)


def test_render_case_verifies_manifest_identity_ground_truth_filename_and_hash(
    tmp_path: Path,
) -> None:
    artwork = ArtworkSpec.model_validate(artwork_payload())
    provisional = render_artwork(artwork, tmp_path / "provisional.png")
    manifest = EvaluationManifestV2.model_validate(manifest_payload(provisional.sha256))
    destination = tmp_path / "accepted" / "clear-composite.png"

    rendered = render_case(manifest.cases[0], destination)

    assert rendered.sha256 == provisional.sha256
    assert destination.exists()

    wrong_ground_truth = copy.deepcopy(manifest_payload(provisional.sha256))
    wrong_ground_truth["cases"][0]["expected_visible_text"]["brand_name"] = [  # type: ignore[index]
        "A model answer must not become ground truth"
    ]
    invalid_case = EvaluationManifestV2.model_validate(wrong_ground_truth).cases[0]
    with pytest.raises(ValueError, match="source text must equal"):
        render_case(invalid_case, tmp_path / "rejected" / "clear-composite.png")


def test_render_case_removes_artifact_when_hash_does_not_match(tmp_path: Path) -> None:
    manifest = EvaluationManifestV2.model_validate(manifest_payload("0" * 64))
    destination = tmp_path / "clear-composite.png"

    with pytest.raises(ValueError, match="hash does not match"):
        render_case(manifest.cases[0], destination)

    assert not destination.exists()


def test_render_case_rejects_unimplemented_randomness(tmp_path: Path) -> None:
    payload = manifest_payload("0" * 64)
    payload["cases"][0]["renderer"]["seed"] = 20260812  # type: ignore[index]
    case = EvaluationManifestV2.model_validate(payload).cases[0]

    with pytest.raises(ValueError, match="seed must be null"):
        render_case(case, tmp_path / "clear-composite.png")
