import argparse
import asyncio
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from app.comparison import CheckStatus, ReviewResult, compare_review
from app.config import PROJECT_ROOT, Settings
from app.extraction import (
    PROMPT_REVISION,
    ExtractionError,
    ExtractionErrorKind,
    OpenAIExtractionAdapter,
    OpenAIExtractionResult,
    OpenAIUsage,
    create_extraction_adapter,
)
from evals.fixtures import EvaluationCase, load_manifest, render_fixture

DEFAULT_MANIFEST = PROJECT_ROOT / "fixtures" / "live-evaluation-v1.json"
PRICING_CHECKED_AT = "2026-08-11"
PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-luna"
LUNA_INPUT_USD_PER_MILLION = Decimal("0.20")
LUNA_CACHED_INPUT_USD_PER_MILLION = Decimal("0.02")
LUNA_OUTPUT_USD_PER_MILLION = Decimal("1.20")


def estimated_cost_usd(model: str, usage: OpenAIUsage | None) -> Decimal | None:
    if model != "gpt-5.6-luna" or usage is None:
        return None
    uncached_tokens = max(0, usage.input_tokens - usage.cached_input_tokens)
    denominator = Decimal(1_000_000)
    cost = (
        Decimal(uncached_tokens) / denominator * LUNA_INPUT_USD_PER_MILLION
        + Decimal(usage.cached_input_tokens) / denominator * LUNA_CACHED_INPUT_USD_PER_MILLION
        + Decimal(usage.output_tokens) / denominator * LUNA_OUTPUT_USD_PER_MILLION
    )
    return cost.quantize(Decimal("0.00000001"))


def _check_statuses(result: ReviewResult) -> dict[str, str]:
    return {check.name.value: check.status.value for check in result.checks}


def evaluate_success(
    case: EvaluationCase,
    extraction: OpenAIExtractionResult,
) -> dict[str, object]:
    review = compare_review(
        case.expected,
        extraction.observations,
        processing_duration_ms=extraction.latency_ms,
    )
    statuses = {check.name: check.status for check in review.checks}
    required_checks_passed = all(
        statuses[name] == expected_status for name, expected_status in case.required_checks.items()
    )
    uncertainty_passed: bool | None = None
    if case.requires_uncertainty:
        field_observations = [
            extraction.observations.brand_name,
            extraction.observations.class_type,
            extraction.observations.alcohol_content,
            extraction.observations.net_contents,
        ]
        no_fabricated_candidates = all(not field.candidates for field in field_observations)
        no_fabricated_warning = extraction.observations.government_warning.text is None
        uncertainty_passed = (
            no_fabricated_candidates
            and no_fabricated_warning
            and all(status == CheckStatus.NEEDS_REVIEW for status in statuses.values())
        )

    passed = (
        review.outcome == case.expected_outcome
        and required_checks_passed
        and uncertainty_passed is not False
    )
    usage = extraction.usage
    cost = estimated_cost_usd(extraction.model, usage)
    return {
        "id": case.id,
        "passed": passed,
        "expected_outcome": case.expected_outcome.value,
        "actual_outcome": review.outcome.value,
        "check_statuses": _check_statuses(review),
        "uncertainty_passed": uncertainty_passed,
        "provider_request_id": extraction.provider_request_id,
        "attempt_count": extraction.attempt_count,
        "latency_ms": extraction.latency_ms,
        "usage": (
            {
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "total_tokens": usage.total_tokens,
            }
            if usage is not None
            else None
        ),
        "estimated_cost_usd": str(cost) if cost is not None else None,
        "error_kind": None,
    }


def evaluate_failure(case: EvaluationCase, error: ExtractionError) -> dict[str, object]:
    return {
        "id": case.id,
        "passed": False,
        "expected_outcome": case.expected_outcome.value,
        "actual_outcome": None,
        "check_statuses": {},
        "uncertainty_passed": False if case.requires_uncertainty else None,
        "provider_request_id": None,
        "attempt_count": None,
        "latency_ms": None,
        "usage": None,
        "estimated_cost_usd": None,
        "error_kind": error.kind.value,
    }


def summarize(records: list[dict[str, object]]) -> dict[str, object]:
    latencies = [record["latency_ms"] for record in records if record["latency_ms"] is not None]
    costs = [
        Decimal(record["estimated_cost_usd"])
        for record in records
        if record["estimated_cost_usd"] is not None
    ]
    malformed_count = sum(
        record["error_kind"] == ExtractionErrorKind.MALFORMED_OUTPUT.value for record in records
    )
    return {
        "fixture_count": len(records),
        "passed_count": sum(record["passed"] is True for record in records),
        "failed_count": sum(record["passed"] is not True for record in records),
        "malformed_output_count": malformed_count,
        "malformed_output_rate": malformed_count / len(records) if records else 0,
        "median_latency_ms": int(median(latencies)) if latencies else None,
        "slowest_latency_ms": max(latencies) if latencies else None,
        "total_estimated_cost_usd": str(sum(costs, Decimal(0))) if costs else None,
    }


async def run_evaluation(
    *,
    settings: Settings,
    manifest_path: Path,
    output_path: Path,
) -> dict[str, object]:
    if output_path.exists():
        raise FileExistsError("Evaluation output already exists; choose a new evidence path.")

    manifest = load_manifest(manifest_path)
    adapter = create_extraction_adapter(settings)
    if not isinstance(adapter, OpenAIExtractionAdapter):
        raise RuntimeError("Live evaluation requires the OpenAI extraction adapter.")

    records: list[dict[str, object]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="treasury-live-eval-") as raw_directory:
            directory = Path(raw_directory)
            for case in manifest.cases:
                prepared = render_fixture(case, directory / f"{case.id}.png")
                try:
                    extraction = await adapter.extract_with_metadata(prepared)
                except ExtractionError as error:
                    records.append(evaluate_failure(case, error))
                else:
                    records.append(evaluate_success(case, extraction))
    finally:
        await adapter.aclose()

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "model": settings.openai_model,
            "image_detail": settings.openai_image_detail,
            "prompt_revision": PROMPT_REVISION,
            "fixture_revision": manifest.revision,
            "fixture_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "max_output_tokens": settings.openai_max_output_tokens,
            "reasoning_effort": "none",
            "store": False,
            "transient_retries": settings.openai_transient_retries,
            "timeout_seconds": settings.extraction_timeout_seconds,
        },
        "pricing": {
            "checked_at": PRICING_CHECKED_AT,
            "source": PRICING_SOURCE,
            "input_usd_per_million": str(LUNA_INPUT_USD_PER_MILLION),
            "cached_input_usd_per_million": str(LUNA_CACHED_INPUT_USD_PER_MILLION),
            "output_usd_per_million": str(LUNA_OUTPUT_USD_PER_MILLION),
            "applies_to_model": "gpt-5.6-luna",
        },
        "summary": summarize(records),
        "cases": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paid OpenAI extraction evaluation on synthetic fixtures."
    )
    parser.add_argument(
        "--confirm-paid-run",
        action="store_true",
        help="Acknowledge that this command makes billable OpenAI API requests.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path for the JSON evidence report.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--model", default=None)
    parser.add_argument("--image-detail", choices=["high", "original"], default=None)
    args = parser.parse_args()
    if not args.confirm_paid_run:
        parser.error("--confirm-paid-run is required for a live provider evaluation")
    return args


def main() -> None:
    args = parse_args()
    configured = Settings(
        extraction_backend="openai",
        live_extraction_enabled=True,
        **({"openai_model": args.model} if args.model else {}),
        **({"openai_image_detail": args.image_detail} if args.image_detail else {}),
    )
    issues = configured.configuration_issues()
    if issues:
        raise SystemExit("Live evaluation configuration is incomplete: " + "; ".join(issues))
    report = asyncio.run(
        run_evaluation(
            settings=configured,
            manifest_path=args.manifest,
            output_path=args.output,
        )
    )
    summary = report["summary"]
    print(
        f"Live evaluation completed: {summary['passed_count']}/{summary['fixture_count']} passed. "
        f"Evidence written to {args.output}."
    )


if __name__ == "__main__":
    main()
