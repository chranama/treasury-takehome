import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from app.comparison import (
    ExtractionObservations,
    FieldObservation,
    Readability,
    TextCandidate,
    Visibility,
    WarningObservation,
)
from app.config import PROJECT_ROOT, Settings
from app.extraction import OpenAIExtractionAdapter, OpenAIExtractionResult
from evals.live import run_evaluation
from evals.manifest import CandidatePolicy, TextPolicy, load_manifest_v2
from evals.renderer import render_case
from evals.visual_evaluation import evaluate_v2_success
from evals.visual_suite import build_manifest, render_manifest_artifacts, write_manifest

COMMITTED_MANIFEST = PROJECT_ROOT / "fixtures" / "hosted-visual-v2.json"


def _observations_for(case) -> ExtractionObservations:
    expected = case.expected_visible_text
    required = case.required_observations
    assert expected is not None
    assert required is not None

    fields = {}
    for name in ("brand_name", "class_type", "alcohol_content", "net_contents"):
        requirement = getattr(required, name)
        candidates = (
            []
            if requirement.candidates == CandidatePolicy.EMPTY
            else [TextCandidate(text=value) for value in getattr(expected, name)]
        )
        fields[name] = FieldObservation(
            candidates=candidates,
            visibility=requirement.visibility[0],
            readability=requirement.readability[0],
        )

    warning_required = required.government_warning
    warning = WarningObservation(
        text=(expected.government_warning if warning_required.text == TextPolicy.EXACT else None),
        heading_text=(
            expected.warning_heading if warning_required.heading_text == TextPolicy.EXACT else None
        ),
        heading_weight=warning_required.heading_weight[0],
        body_weight=warning_required.body_weight[0],
        visibility=warning_required.visibility[0],
        readability=warning_required.readability[0],
    )
    return ExtractionObservations(**fields, government_warning=warning)


def _extraction(case) -> OpenAIExtractionResult:
    return OpenAIExtractionResult(
        observations=_observations_for(case),
        provider_request_id="resp_synthetic_test",
        model="gpt-5.6-luna",
        prompt_revision="label-observations-v2",
        image_detail="high",
        requested_service_tier="default",
        response_service_tier="default",
        attempt_count=1,
        latency_ms=100,
        usage=None,
    )


def test_committed_v2_manifest_matches_deterministic_suite_source(tmp_path: Path) -> None:
    generated = tmp_path / "hosted-visual-v2.json"
    write_manifest(generated)

    assert generated.read_bytes() == COMMITTED_MANIFEST.read_bytes()


def test_v2_suite_covers_the_required_visual_matrix() -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)

    assert len(manifest.cases) == 18
    assert {case.id for case in manifest.cases} == {
        "clear-composite",
        "clear-single-panel",
        "brand-format-variation",
        "material-brand-difference",
        "material-class-difference",
        "proof-only",
        "conflicting-alcohol",
        "equivalent-net-contents",
        "material-net-mismatch",
        "missing-warning",
        "altered-warning",
        "incorrect-warning-weight",
        "ambiguous-brand-and-quantity",
        "rotated-back-panel",
        "small-warning-threshold",
        "obscured-warning",
        "degraded-unreadable",
        "near-dimension-bound",
    }


def test_every_v2_artifact_renders_to_its_committed_hash(tmp_path: Path) -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)

    rendered = {
        case.id: render_case(case, tmp_path / case.artifacts[0].filename).sha256
        for case in manifest.cases
    }

    assert rendered == {case.id: case.artifacts[0].sha256 for case in manifest.cases}


def test_manual_inspection_renderer_writes_the_complete_hash_verified_suite(
    tmp_path: Path,
) -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)

    render_manifest_artifacts(manifest, tmp_path)

    assert {path.name for path in tmp_path.glob("*.png")} == {
        case.artifacts[0].filename for case in manifest.cases
    }


def test_independently_declared_ideal_observations_pass_every_v2_gate() -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)

    records = [evaluate_v2_success(case, _extraction(case)) for case in manifest.cases]

    assert all(record["passed"] is True for record in records)
    assert all(record["artifact_sha256"] for record in records)
    assert all(all(record["observation_gate"].values()) for record in records)
    assert all(all(record["review_gate"].values()) for record in records)


def test_v2_gate_rejects_fabricated_or_unlisted_candidates() -> None:
    manifest = build_manifest()
    clear = next(case for case in manifest.cases if case.id == "clear-composite")
    extraction = _extraction(clear)
    extraction.observations.brand_name.candidates.append(TextCandidate(text="Invented Brand"))

    record = evaluate_v2_success(clear, extraction)

    assert record["passed"] is False
    assert record["observation_gate"]["brand_name.candidates"] is False


def test_v2_gate_requires_uncertainty_for_degraded_artwork() -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)
    degraded = next(case for case in manifest.cases if case.id == "degraded-unreadable")
    extraction = _extraction(degraded)
    readable = FieldObservation(
        candidates=[TextCandidate(text="OLD TOM")],
        visibility=Visibility.VISIBLE,
        readability=Readability.READABLE,
    )
    extraction.observations.brand_name = readable

    record = evaluate_v2_success(degraded, extraction)

    assert record["passed"] is False
    assert record["observation_gate"]["brand_name.candidates"] is False
    assert record["observation_gate"]["brand_name.visibility"] is False


def test_missing_warning_accepts_conservative_uncertainty_without_losing_review() -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)
    missing = next(case for case in manifest.cases if case.id == "missing-warning")
    extraction = _extraction(missing)
    extraction.observations.government_warning.visibility = Visibility.UNCERTAIN

    record = evaluate_v2_success(missing, extraction)

    assert record["passed"] is True
    assert record["actual_outcome"] == "needs_review"
    assert record["check_statuses"]["government_warning"] == "needs_review"


def test_live_harness_runs_v2_rendering_and_evaluation_without_legacy_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)
    cases = {case.artifacts[0].filename: case for case in manifest.cases}
    close = AsyncMock()
    adapter = OpenAIExtractionAdapter(
        client=cast(AsyncOpenAI, SimpleNamespace(close=close)),
        model="gpt-5.6-luna",
    )

    async def extract(_, prepared) -> OpenAIExtractionResult:
        case = cases[prepared.path.name]
        return _extraction(case)

    monkeypatch.setattr("evals.live.create_extraction_adapter", lambda _: adapter)
    monkeypatch.setattr(OpenAIExtractionAdapter, "extract_with_metadata", extract)
    monkeypatch.setattr(
        "evals.live.source_state",
        lambda: {"git_commit": "a" * 40, "dirty": False},
    )
    output = tmp_path / "v2-report.json"

    report = asyncio.run(
        run_evaluation(
            settings=Settings(_env_file=None),
            manifest_path=COMMITTED_MANIFEST,
            output_path=output,
        )
    )

    assert report["configuration"]["fixture_revision"] == "hosted-visual-v2"
    assert report["summary"]["fixture_count"] == 18
    assert report["summary"]["passed_count"] == 18
    assert all(case["artifact_sha256"] for case in report["cases"])
    assert output.is_file()
    close.assert_awaited_once_with()
