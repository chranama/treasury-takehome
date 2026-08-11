import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from app.comparison import (
    ExtractionObservations,
    FieldObservation,
    Readability,
    TextCandidate,
    TextWeight,
    Visibility,
    WarningObservation,
)
from app.config import Settings
from app.extraction import OpenAIExtractionResult, OpenAIUsage
from evals.fixtures import load_manifest
from evals.live import estimated_cost_usd, evaluate_success, run_evaluation, summarize

MANIFEST = Path(__file__).resolve().parents[2] / "fixtures" / "live-evaluation-v1.json"


def test_cost_uses_versioned_uncached_cached_and_output_rates() -> None:
    usage = OpenAIUsage(
        input_tokens=1_000_000,
        cached_input_tokens=250_000,
        output_tokens=100_000,
        reasoning_tokens=10_000,
        total_tokens=1_100_000,
    )

    assert estimated_cost_usd("gpt-5.6-luna", usage) == Decimal("0.27500000")
    assert estimated_cost_usd("another-model", usage) is None
    assert estimated_cost_usd("gpt-5.6-luna", None) is None


def test_unreadable_case_rejects_fabricated_candidates() -> None:
    case = load_manifest(MANIFEST).cases[-1]
    unreadable_field = FieldObservation(
        candidates=[],
        visibility=Visibility.UNCERTAIN,
        readability=Readability.UNREADABLE,
    )
    observations = ExtractionObservations(
        brand_name=unreadable_field,
        class_type=unreadable_field,
        alcohol_content=unreadable_field,
        net_contents=unreadable_field,
        government_warning=WarningObservation(
            text=None,
            heading_text=None,
            heading_weight=TextWeight.UNCERTAIN,
            body_weight=TextWeight.UNCERTAIN,
            visibility=Visibility.UNCERTAIN,
            readability=Readability.UNREADABLE,
        ),
    )
    extraction = OpenAIExtractionResult(
        observations=observations,
        provider_request_id="resp_test",
        model="gpt-5.6-luna",
        prompt_revision="test",
        image_detail="high",
        attempt_count=1,
        latency_ms=100,
        usage=None,
    )

    record = evaluate_success(case, extraction)

    assert record["passed"] is True
    assert record["uncertainty_passed"] is True

    observations.brand_name = FieldObservation(
        candidates=[TextCandidate(text="Treasury Reserve")],
        visibility=Visibility.VISIBLE,
        readability=Readability.READABLE,
    )
    record = evaluate_success(case, extraction)
    assert record["passed"] is False


def test_summary_counts_failures_and_malformed_outputs() -> None:
    records = [
        {
            "passed": True,
            "latency_ms": 100,
            "estimated_cost_usd": "0.001",
            "error_kind": None,
        },
        {
            "passed": False,
            "latency_ms": None,
            "estimated_cost_usd": None,
            "error_kind": "malformed_output",
        },
    ]

    summary = summarize(records)

    assert summary["fixture_count"] == 2
    assert summary["passed_count"] == 1
    assert summary["malformed_output_count"] == 1
    assert summary["malformed_output_rate"] == 0.5
    assert summary["median_latency_ms"] == 100
    assert summary["total_estimated_cost_usd"] == "0.001"


def test_live_evaluation_refuses_to_overwrite_evidence(tmp_path: Path) -> None:
    output = tmp_path / "existing.json"
    output.write_text("preserve me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        asyncio.run(
            run_evaluation(
                settings=Settings(_env_file=None),
                manifest_path=MANIFEST,
                output_path=output,
            )
        )

    assert output.read_text(encoding="utf-8") == "preserve me"
