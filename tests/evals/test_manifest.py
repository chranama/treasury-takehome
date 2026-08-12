import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.fixtures import load_manifest
from evals.manifest import EvaluationManifestV2, load_manifest_v2, manifest_schema_v2

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_MANIFEST = PROJECT_ROOT / "fixtures" / "live-evaluation-v1.json"
LEGACY_MANIFEST_SHA256 = "9521ca3e94a3ce88bd14fc783d7905b7a317454817c593294a622755873a1797"


def hosted_manifest_payload() -> dict[str, object]:
    readable_field = {
        "candidates": "exact",
        "visibility": ["visible"],
        "readability": ["readable"],
    }
    return {
        "schema_version": 2,
        "revision": "hosted-visual-v2",
        "owner": "hosted_extraction",
        "purpose": "Evaluate visible label observations on synthetic artwork.",
        "cases": [
            {
                "id": "clear-matching-label",
                "purpose": "Establish the clear five-check baseline.",
                "families": ["hosted_model_visual"],
                "layers": [
                    "manifest_schema",
                    "deterministic_rendering",
                    "manual_visual_inspection",
                    "live_provider",
                ],
                "renderer": {
                    "id": "synthetic-label",
                    "version": "2",
                    "font_identity": "pillow-embedded-aileron-regular",
                    "seed": None,
                },
                "artwork": {
                    "layout": "front-back-composite",
                    "warning_variant": "required",
                },
                "expected_visible_text": {
                    "brand_name": ["Treasury Reserve"],
                    "class_type": ["Kentucky Straight Bourbon Whiskey"],
                    "alcohol_content": ["45% Alc./Vol. (90 Proof)"],
                    "net_contents": ["750 mL"],
                    "government_warning": "GOVERNMENT WARNING: synthetic exact text",
                    "warning_heading": "GOVERNMENT WARNING",
                },
                "expected_application": {
                    "brand_name": "Treasury Reserve",
                    "class_type": "Kentucky Straight Bourbon Whiskey",
                    "abv": "45",
                    "net_contents": {"value": "750", "unit": "mL"},
                },
                "required_observations": {
                    "brand_name": readable_field,
                    "class_type": readable_field,
                    "alcohol_content": readable_field,
                    "net_contents": readable_field,
                    "government_warning": {
                        "text": "exact",
                        "heading_text": "exact",
                        "heading_weight": ["bold"],
                        "body_weight": ["not_bold"],
                        "visibility": ["visible"],
                        "readability": ["readable"],
                    },
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
                        "filename": "clear-matching-label.png",
                        "media_type": "image/png",
                        "sha256": "a" * 64,
                    }
                ],
            }
        ],
    }


def test_v2_manifest_validates_complete_hosted_case(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(hosted_manifest_payload()), encoding="utf-8")

    manifest = load_manifest_v2(path)

    assert manifest.schema_version == 2
    assert manifest.owner.value == "hosted_extraction"
    assert manifest.cases[0].expected_review.checks.government_warning.value == "match"
    assert manifest.cases[0].artifacts[0].sha256 == "a" * 64


def test_v2_schema_is_strict_and_exposes_shared_contract() -> None:
    schema = manifest_schema_v2()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "revision",
        "owner",
        "purpose",
        "cases",
    }
    assert "EvaluationCaseV2" in schema["$defs"]
    assert "ExpectedCheckStatuses" in schema["$defs"]


def test_v2_manifest_rejects_incomplete_check_expectations() -> None:
    payload = hosted_manifest_payload()
    del payload["cases"][0]["expected_review"]["checks"]["government_warning"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="government_warning"):
        EvaluationManifestV2.model_validate(payload)


def test_v2_manifest_rejects_contradictory_overall_outcome() -> None:
    payload = hosted_manifest_payload()
    payload["cases"][0]["expected_review"]["checks"]["net_contents"] = "mismatch"  # type: ignore[index]

    with pytest.raises(ValidationError, match="five matching checks"):
        EvaluationManifestV2.model_validate(payload)


def test_v2_manifest_rejects_duplicate_allowed_check_statuses() -> None:
    payload = hosted_manifest_payload()
    payload["cases"][0]["expected_review"]["checks"]["government_warning"] = [  # type: ignore[index]
        "match",
        "match",
    ]

    with pytest.raises(ValidationError, match="allowed check statuses must be unique"):
        EvaluationManifestV2.model_validate(payload)


def test_v2_manifest_rejects_owner_family_mismatch() -> None:
    payload = hosted_manifest_payload()
    payload["owner"] = "comparison"

    with pytest.raises(ValidationError, match="requires family deterministic_domain"):
        EvaluationManifestV2.model_validate(payload)


@pytest.mark.parametrize(
    "missing_field",
    [
        "renderer",
        "artwork",
        "expected_visible_text",
        "expected_application",
        "required_observations",
        "expected_review",
        "uncertainty",
        "artifacts",
    ],
)
def test_v2_hosted_case_rejects_missing_evaluation_metadata(missing_field: str) -> None:
    payload = hosted_manifest_payload()
    del payload["cases"][0][missing_field]  # type: ignore[index]

    with pytest.raises(ValidationError, match="hosted-model visual cases require"):
        EvaluationManifestV2.model_validate(payload)


def test_v2_manifest_rejects_duplicate_case_ids() -> None:
    payload = hosted_manifest_payload()
    payload["cases"].append(copy.deepcopy(payload["cases"][0]))  # type: ignore[union-attr,index]

    with pytest.raises(ValidationError, match="case ids must be unique"):
        EvaluationManifestV2.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "Not Stable", "lowercase hyphenated"),
        ("families", ["hosted_model_visual", "hosted_model_visual"], "must be unique"),
        ("artifacts", [], "hosted-model visual cases require"),
    ],
)
def test_v2_manifest_rejects_unstable_or_ambiguous_case_metadata(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = hosted_manifest_payload()
    payload["cases"][0][field] = value  # type: ignore[index]

    with pytest.raises(ValidationError, match=message):
        EvaluationManifestV2.model_validate(payload)


def test_v2_manifest_rejects_invalid_artifact_hash() -> None:
    payload = hosted_manifest_payload()
    payload["cases"][0]["artifacts"][0]["sha256"] = "ABC123"  # type: ignore[index]

    with pytest.raises(ValidationError, match="64 lowercase hexadecimal"):
        EvaluationManifestV2.model_validate(payload)


def test_v2_manifest_rejects_unknown_fields() -> None:
    payload = hosted_manifest_payload()
    payload["cases"][0]["model_answer"] = "do not use provider output as ground truth"  # type: ignore[index]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvaluationManifestV2.model_validate(payload)


def test_v2_live_provider_layer_always_requires_observation_properties() -> None:
    payload = hosted_manifest_payload()
    payload["owner"] = "comparison"
    case = payload["cases"][0]  # type: ignore[index]
    case["families"] = ["deterministic_domain"]
    del case["required_observations"]

    with pytest.raises(ValidationError, match="live-provider cases require"):
        EvaluationManifestV2.model_validate(payload)


def test_legacy_manifest_remains_byte_identical_and_loadable() -> None:
    assert hashlib.sha256(LEGACY_MANIFEST.read_bytes()).hexdigest() == LEGACY_MANIFEST_SHA256

    manifest = load_manifest(LEGACY_MANIFEST)

    assert manifest.revision == "live-evaluation-v1"
    assert [case.id for case in manifest.cases] == [
        "clear-matching-label",
        "mismatched-net-contents",
        "altered-government-warning",
        "unreadable-label",
    ]
