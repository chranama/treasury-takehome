import argparse
import asyncio
import hashlib
import json
import tempfile
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.config import Settings
from app.extraction import (
    PROMPT_REVISION,
    ExtractionError,
    OpenAIExtractionAdapter,
    create_extraction_adapter,
)
from evals.fixtures import load_manifest, render_fixture
from evals.live import (
    DEFAULT_MANIFEST,
    LUNA_CACHED_INPUT_USD_PER_MILLION,
    LUNA_FAST_RATE_MULTIPLIER,
    LUNA_INPUT_USD_PER_MILLION,
    LUNA_OUTPUT_USD_PER_MILLION,
    PRICING_CHECKED_AT,
    PRICING_SOURCE,
    evaluate_failure,
    evaluate_success,
    source_state,
    summarize,
)

TIERS = ("default", "fast")


def tier_order(run_index: int, case_index: int) -> tuple[str, str]:
    """Alternate which tier goes first for every adjacent case pair."""

    return TIERS if (run_index + case_index) % 2 == 0 else tuple(reversed(TIERS))


def _ratio(numerator: int | str | None, denominator: int | str | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    denominator_decimal = Decimal(str(denominator))
    if denominator_decimal == 0:
        return None
    return float(Decimal(str(numerator)) / denominator_decimal)


def _percent_change(fast: int | None, standard: int | None) -> float | None:
    ratio = _ratio(fast, standard)
    return None if ratio is None else round((ratio - 1) * 100, 2)


def compare_tiers(
    standard_summary: dict[str, object],
    fast_summary: dict[str, object],
) -> dict[str, object]:
    standard_cost = standard_summary["total_estimated_cost_usd"]
    fast_cost = fast_summary["total_estimated_cost_usd"]
    return {
        "median_latency_change_percent": _percent_change(
            fast_summary["median_latency_ms"],  # type: ignore[arg-type]
            standard_summary["median_latency_ms"],  # type: ignore[arg-type]
        ),
        "p90_latency_change_percent": _percent_change(
            fast_summary["p90_latency_ms"],  # type: ignore[arg-type]
            standard_summary["p90_latency_ms"],  # type: ignore[arg-type]
        ),
        "p95_latency_change_percent": _percent_change(
            fast_summary["p95_latency_ms"],  # type: ignore[arg-type]
            standard_summary["p95_latency_ms"],  # type: ignore[arg-type]
        ),
        "cost_ratio_fast_to_standard": _ratio(fast_cost, standard_cost),
    }


def _tier_summary(records: list[dict[str, object]]) -> dict[str, object]:
    summary = summarize(records)
    summary["returned_service_tier_counts"] = dict(
        Counter(
            str(record["response_service_tier"])
            for record in records
            if record["response_service_tier"] is not None
        )
    )
    summary["retry_count"] = sum(
        max(0, int(record["attempt_count"]) - 1)
        for record in records
        if record["attempt_count"] is not None
    )
    return summary


async def run_benchmark(
    *,
    settings: Settings,
    manifest_path: Path,
    output_path: Path,
    runs_per_tier: int,
) -> dict[str, object]:
    if output_path.exists():
        raise FileExistsError("Benchmark output already exists; choose a new evidence path.")
    if runs_per_tier < 1:
        raise ValueError("runs per tier must be positive")

    manifest = load_manifest(manifest_path)
    adapters: dict[str, OpenAIExtractionAdapter] = {}
    for tier in TIERS:
        tier_settings = settings.model_copy(update={"openai_service_tier": tier})
        adapter = create_extraction_adapter(tier_settings)
        if not isinstance(adapter, OpenAIExtractionAdapter):
            raise RuntimeError("Tier benchmark requires the OpenAI extraction adapter.")
        adapters[tier] = adapter

    aggregate_records: dict[str, list[dict[str, object]]] = {tier: [] for tier in TIERS}
    runs: list[dict[str, object]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="treasury-tier-benchmark-") as raw_directory:
            directory = Path(raw_directory)
            prepared_images = {
                case.id: render_fixture(case, directory / f"{case.id}.png")
                for case in manifest.cases
            }
            for run_index in range(runs_per_tier):
                paired_cases: list[dict[str, object]] = []
                for case_index, case in enumerate(manifest.cases):
                    order = tier_order(run_index, case_index)
                    tier_records: dict[str, dict[str, object]] = {}
                    for tier in order:
                        adapter = adapters[tier]
                        try:
                            extraction = await adapter.extract_with_metadata(
                                prepared_images[case.id]
                            )
                        except ExtractionError as error:
                            record = evaluate_failure(case, error, tier)
                        else:
                            record = evaluate_success(case, extraction)
                        aggregate_records[tier].append(record)
                        tier_records[tier] = record
                    paired_cases.append(
                        {
                            "case_id": case.id,
                            "request_order": list(order),
                            "tiers": tier_records,
                        }
                    )
                runs.append({"run_number": run_index + 1, "paired_cases": paired_cases})
    finally:
        await asyncio.gather(*(adapter.aclose() for adapter in adapters.values()))

    tier_summaries = {tier: _tier_summary(aggregate_records[tier]) for tier in TIERS}
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source_state(),
        "design": {
            "runs_per_tier": runs_per_tier,
            "cases_per_run": len(manifest.cases),
            "requests_per_tier": runs_per_tier * len(manifest.cases),
            "pairing": "same fixture, adjacent requests",
            "ordering": "alternates requested tier first by run and fixture",
        },
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
            "standard_input_usd_per_million": str(LUNA_INPUT_USD_PER_MILLION),
            "standard_cached_input_usd_per_million": str(LUNA_CACHED_INPUT_USD_PER_MILLION),
            "standard_output_usd_per_million": str(LUNA_OUTPUT_USD_PER_MILLION),
            "fast_rate_multiplier": str(LUNA_FAST_RATE_MULTIPLIER),
            "applies_to_model": "gpt-5.6-luna",
        },
        "tier_summaries": tier_summaries,
        "comparison": compare_tiers(tier_summaries["default"], tier_summaries["fast"]),
        "runs": runs,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare paid Luna Standard and Fast service tiers on synthetic fixtures."
    )
    parser.add_argument(
        "--confirm-paid-run",
        action="store_true",
        help="Acknowledge that this command makes billable OpenAI API requests.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--runs-per-tier", type=int, default=10)
    args = parser.parse_args()
    if not args.confirm_paid_run:
        parser.error("--confirm-paid-run is required for a paid tier benchmark")
    if args.runs_per_tier < 1:
        parser.error("--runs-per-tier must be positive")
    return args


def main() -> None:
    args = parse_args()
    configured = Settings(extraction_backend="openai", live_extraction_enabled=True)
    issues = configured.configuration_issues()
    if issues:
        raise SystemExit("Tier benchmark configuration is incomplete: " + "; ".join(issues))
    report = asyncio.run(
        run_benchmark(
            settings=configured,
            manifest_path=args.manifest,
            output_path=args.output,
            runs_per_tier=args.runs_per_tier,
        )
    )
    standard = report["tier_summaries"]["default"]
    fast = report["tier_summaries"]["fast"]
    print(
        "Tier benchmark completed: "
        f"Standard {standard['passed_count']}/{standard['fixture_count']} passed; "
        f"Fast {fast['passed_count']}/{fast['fixture_count']} passed. "
        f"Evidence written to {args.output}."
    )


if __name__ == "__main__":
    main()
