from app.comparison import CheckStatus, ExtractionObservations, compare_review
from app.comparison.normalization import normalize_warning_heading, normalize_warning_text
from app.extraction import OpenAIExtractionResult, estimated_cost_usd
from evals.manifest import (
    CandidatePolicy,
    EvaluationCaseV2,
    TextPolicy,
    UncertaintyExpectation,
)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _candidate_gate(
    policy: CandidatePolicy,
    expected: list[str],
    actual: list[str],
) -> bool:
    expected_values = {_normalized_text(value) for value in expected}
    actual_values = {_normalized_text(value) for value in actual}
    if policy == CandidatePolicy.EMPTY:
        return not actual_values
    if policy == CandidatePolicy.EXACT:
        return actual_values == expected_values
    return expected_values <= actual_values


def _optional_text_gate(
    policy: TextPolicy,
    expected: str | None,
    actual: str | None,
    *,
    heading: bool = False,
) -> bool:
    if policy == TextPolicy.ANY:
        return True
    if policy == TextPolicy.ABSENT:
        return actual is None
    if expected is None or actual is None:
        return False
    normalize = normalize_warning_heading if heading else normalize_warning_text
    return normalize(actual) == normalize(expected)


def _check_status_gate(
    expected: CheckStatus | list[CheckStatus],
    actual: CheckStatus,
) -> bool:
    allowed = expected if isinstance(expected, list) else [expected]
    return actual in allowed


def observation_gate(
    case: EvaluationCaseV2,
    observations: ExtractionObservations,
) -> dict[str, bool]:
    expected = case.expected_visible_text
    required = case.required_observations
    if expected is None or required is None:
        raise ValueError("v2 live evaluation requires visible-text and observation ground truth")

    results: dict[str, bool] = {}
    for name in ("brand_name", "class_type", "alcohol_content", "net_contents"):
        actual_field = getattr(observations, name)
        required_field = getattr(required, name)
        expected_candidates = getattr(expected, name)
        results[f"{name}.candidates"] = _candidate_gate(
            required_field.candidates,
            expected_candidates,
            [candidate.text for candidate in actual_field.candidates],
        )
        results[f"{name}.visibility"] = actual_field.visibility in required_field.visibility
        results[f"{name}.readability"] = actual_field.readability in required_field.readability

    warning = observations.government_warning
    warning_required = required.government_warning
    results["government_warning.text"] = _optional_text_gate(
        warning_required.text,
        expected.government_warning,
        warning.text,
    )
    results["government_warning.heading_text"] = _optional_text_gate(
        warning_required.heading_text,
        expected.warning_heading,
        warning.heading_text,
        heading=True,
    )
    results["government_warning.heading_weight"] = (
        warning.heading_weight in warning_required.heading_weight
    )
    results["government_warning.body_weight"] = warning.body_weight in warning_required.body_weight
    results["government_warning.visibility"] = warning.visibility in warning_required.visibility
    results["government_warning.readability"] = warning.readability in warning_required.readability
    return results


def uncertainty_gate(case: EvaluationCaseV2, observations: ExtractionObservations) -> bool:
    uncertain_state = any(
        field.visibility.value == "uncertain" or field.readability.value == "uncertain"
        for field in (
            observations.brand_name,
            observations.class_type,
            observations.alcohol_content,
            observations.net_contents,
        )
    ) or (
        observations.government_warning.visibility.value == "uncertain"
        or observations.government_warning.readability.value == "uncertain"
    )
    degraded_evidence = any(
        field.visibility.value == "uncertain" or field.readability.value != "readable"
        for field in (
            observations.brand_name,
            observations.class_type,
            observations.alcohol_content,
            observations.net_contents,
        )
    ) or (
        observations.government_warning.visibility.value == "uncertain"
        or observations.government_warning.readability.value != "readable"
    )
    if case.uncertainty == UncertaintyExpectation.REQUIRED:
        return degraded_evidence
    if case.uncertainty == UncertaintyExpectation.FORBIDDEN:
        return not uncertain_state
    return True


def evaluate_v2_success(
    case: EvaluationCaseV2,
    extraction: OpenAIExtractionResult,
) -> dict[str, object]:
    expected_application = case.expected_application
    if expected_application is None:
        raise ValueError("v2 live evaluation requires expected application values")
    review = compare_review(
        expected_application,
        extraction.observations,
        processing_duration_ms=extraction.latency_ms,
    )
    actual_checks = {check.name.value: check.status for check in review.checks}
    expected_checks = case.expected_review.checks.model_dump(mode="python")
    check_gate = {
        name: _check_status_gate(expected, actual_checks[name])
        for name, expected in expected_checks.items()
    }
    observations = observation_gate(case, extraction.observations)
    uncertainty_passed = uncertainty_gate(case, extraction.observations)
    review_passed = review.outcome == case.expected_review.outcome and all(check_gate.values())
    usage = extraction.usage
    billed_service_tier = extraction.response_service_tier or extraction.requested_service_tier
    cost = estimated_cost_usd(extraction.model, usage, billed_service_tier)
    return {
        "id": case.id,
        "artifact_sha256": case.artifacts[0].sha256,
        "passed": all(observations.values()) and review_passed and uncertainty_passed,
        "observation_gate": observations,
        "review_gate": {
            "outcome": review.outcome == case.expected_review.outcome,
            **check_gate,
        },
        "uncertainty_passed": uncertainty_passed,
        "expected_outcome": case.expected_review.outcome.value,
        "actual_outcome": review.outcome.value,
        "check_statuses": {check.name.value: check.status.value for check in review.checks},
        "observations": extraction.observations.model_dump(mode="json"),
        "provider_request_id": extraction.provider_request_id,
        "requested_service_tier": extraction.requested_service_tier,
        "response_service_tier": extraction.response_service_tier,
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


def evaluate_v2_failure(
    case: EvaluationCaseV2,
    *,
    error_kind: str,
    requested_service_tier: str | None,
) -> dict[str, object]:
    return {
        "id": case.id,
        "artifact_sha256": case.artifacts[0].sha256,
        "passed": False,
        "observation_gate": {},
        "review_gate": {},
        "uncertainty_passed": False,
        "expected_outcome": case.expected_review.outcome.value,
        "actual_outcome": None,
        "check_statuses": {},
        "observations": None,
        "provider_request_id": None,
        "requested_service_tier": requested_service_tier,
        "response_service_tier": None,
        "attempt_count": None,
        "latency_ms": None,
        "usage": None,
        "estimated_cost_usd": None,
        "error_kind": error_kind,
    }
