from collections.abc import Callable
from decimal import Decimal

from app.comparison.constants import (
    GOVERNMENT_WARNING_HEADING,
    GOVERNMENT_WARNING_TEXT,
    UNSCALED_IMAGE_WARNING_LIMITATIONS,
)
from app.comparison.models import (
    CheckName,
    CheckResult,
    CheckStatus,
    ExpectedNetContents,
    ExpectedReview,
    ExtractionObservations,
    FieldObservation,
    NetContentsUnit,
    OverallOutcome,
    Readability,
    ReviewResult,
    TextWeight,
    Visibility,
    WarningObservation,
)
from app.comparison.normalization import (
    format_decimal,
    normalize_brand,
    normalize_class_type,
    normalize_warning_heading,
    normalize_warning_text,
    ordered_unique,
)
from app.comparison.parsing import parse_alcohol_statements, parse_net_contents_statements


def _raw_candidates(observation: FieldObservation) -> list[str]:
    return [candidate.text for candidate in observation.candidates]


def _unavailable_field_result(
    *,
    name: CheckName,
    label: str,
    expected_value: str,
    normalized_expected: str,
    observation: FieldObservation,
) -> CheckResult | None:
    extracted = _raw_candidates(observation)
    if observation.visibility == Visibility.NOT_VISIBLE:
        reason = f"{label} was not visible in the supplied artwork."
    elif observation.visibility == Visibility.UNCERTAIN:
        reason = f"It is uncertain whether {label.lower()} is visible."
    elif observation.readability != Readability.READABLE:
        reason = f"{label} was not clearly readable."
    elif not extracted:
        reason = f"No readable {label.lower()} candidate was extracted."
    else:
        return None

    return CheckResult(
        name=name,
        status=CheckStatus.NEEDS_REVIEW,
        expected_value=expected_value,
        extracted_values=extracted,
        normalized_expected=normalized_expected,
        reason=reason,
    )


def _compare_conservative_text(
    *,
    name: CheckName,
    label: str,
    expected: str,
    observation: FieldObservation,
    normalize: Callable[[str], str],
) -> CheckResult:
    expected_normalized = normalize(expected)
    unavailable = _unavailable_field_result(
        name=name,
        label=label,
        expected_value=expected,
        normalized_expected=expected_normalized,
        observation=observation,
    )
    if unavailable is not None:
        return unavailable

    extracted = _raw_candidates(observation)
    normalized = ordered_unique([normalize(value) for value in extracted])
    if len(normalized) > 1:
        return CheckResult(
            name=name,
            status=CheckStatus.NEEDS_REVIEW,
            expected_value=expected,
            extracted_values=extracted,
            normalized_expected=expected_normalized,
            normalized_extracted=normalized,
            reason=f"Multiple conflicting {label.lower()} candidates were visible.",
        )

    if normalized[0] == expected_normalized:
        status = CheckStatus.MATCH
        reason = f"{label} matches after the permitted text normalization."
    else:
        status = CheckStatus.NEEDS_REVIEW
        reason = f"Visible {label.lower()} differs materially from the expected value."

    return CheckResult(
        name=name,
        status=status,
        expected_value=expected,
        extracted_values=extracted,
        normalized_expected=expected_normalized,
        normalized_extracted=normalized,
        reason=reason,
    )


def compare_brand(expected: str, observation: FieldObservation) -> CheckResult:
    return _compare_conservative_text(
        name=CheckName.BRAND_NAME,
        label="Brand name",
        expected=expected,
        observation=observation,
        normalize=normalize_brand,
    )


def compare_class_type(expected: str, observation: FieldObservation) -> CheckResult:
    return _compare_conservative_text(
        name=CheckName.CLASS_TYPE,
        label="Class/type",
        expected=expected,
        observation=observation,
        normalize=normalize_class_type,
    )


def compare_alcohol_content(expected_abv: Decimal, observation: FieldObservation) -> CheckResult:
    expected_value = f"{format_decimal(expected_abv)}%"
    expected_normalized = f"{format_decimal(expected_abv)}% ABV"
    unavailable = _unavailable_field_result(
        name=CheckName.ALCOHOL_CONTENT,
        label="Alcohol content",
        expected_value=expected_value,
        normalized_expected=expected_normalized,
        observation=observation,
    )
    if unavailable is not None:
        return unavailable

    extracted = _raw_candidates(observation)
    parsed = parse_alcohol_statements(extracted)
    normalized = [f"{format_decimal(value)}% ABV" for value in parsed.abv_values]

    if parsed.has_unrecognized_statement or parsed.has_out_of_range_value or not parsed.abv_values:
        status = CheckStatus.NEEDS_REVIEW
        reason = "At least one alcohol statement could not be interpreted unambiguously."
    elif parsed.has_conflict:
        status = CheckStatus.NEEDS_REVIEW
        reason = "Visible alcohol statements resolve to conflicting ABV values."
    elif parsed.abv_values[0] != expected_abv:
        status = CheckStatus.MISMATCH
        reason = "Visible alcohol content differs from the expected ABV."
    else:
        status = CheckStatus.MATCH
        reason = (
            "Visible proof converts to the expected ABV using ABV = proof / 2."
            if parsed.used_proof
            else "Visible ABV matches the expected value exactly."
        )

    return CheckResult(
        name=CheckName.ALCOHOL_CONTENT,
        status=status,
        expected_value=expected_value,
        extracted_values=extracted,
        normalized_expected=expected_normalized,
        normalized_extracted=normalized,
        reason=reason,
    )


def _expected_net_contents_values(expected: ExpectedNetContents) -> tuple[str, str, Decimal]:
    display = f"{format_decimal(expected.value)} {expected.unit.value}"
    milliliters = (
        expected.value * Decimal(1000) if expected.unit == NetContentsUnit.LITER else expected.value
    )
    normalized = f"{format_decimal(milliliters)} mL"
    return display, normalized, milliliters


def compare_net_contents(
    expected: ExpectedNetContents,
    observation: FieldObservation,
) -> CheckResult:
    expected_value, expected_normalized, expected_milliliters = _expected_net_contents_values(
        expected
    )
    unavailable = _unavailable_field_result(
        name=CheckName.NET_CONTENTS,
        label="Net contents",
        expected_value=expected_value,
        normalized_expected=expected_normalized,
        observation=observation,
    )
    if unavailable is not None:
        return unavailable

    extracted = _raw_candidates(observation)
    parsed = parse_net_contents_statements(extracted)
    normalized = [f"{format_decimal(value)} mL" for value in parsed.milliliter_values]

    if (
        parsed.has_unrecognized_statement
        or parsed.has_nonpositive_value
        or not parsed.milliliter_values
    ):
        status = CheckStatus.NEEDS_REVIEW
        reason = "At least one net-contents statement could not be interpreted unambiguously."
    elif parsed.has_conflict:
        status = CheckStatus.NEEDS_REVIEW
        reason = "Visible net-contents statements resolve to conflicting quantities."
    elif parsed.milliliter_values[0] != expected_milliliters:
        status = CheckStatus.MISMATCH
        reason = "Visible net contents differ from the expected quantity."
    else:
        status = CheckStatus.MATCH
        reason = "Visible net contents equal the expected quantity after metric conversion."

    return CheckResult(
        name=CheckName.NET_CONTENTS,
        status=status,
        expected_value=expected_value,
        extracted_values=extracted,
        normalized_expected=expected_normalized,
        normalized_extracted=normalized,
        reason=reason,
    )


def _warning_result(
    observation: WarningObservation,
    *,
    status: CheckStatus,
    reason: str,
) -> CheckResult:
    extracted = [observation.text] if observation.text is not None else []
    normalized = [normalize_warning_text(observation.text)] if observation.text is not None else []
    return CheckResult(
        name=CheckName.GOVERNMENT_WARNING,
        status=status,
        expected_value=GOVERNMENT_WARNING_TEXT,
        extracted_values=extracted,
        normalized_expected=normalize_warning_text(GOVERNMENT_WARNING_TEXT),
        normalized_extracted=normalized,
        reason=reason,
        limitations=list(UNSCALED_IMAGE_WARNING_LIMITATIONS),
    )


def compare_government_warning(observation: WarningObservation) -> CheckResult:
    if observation.visibility == Visibility.NOT_VISIBLE:
        return _warning_result(
            observation,
            status=CheckStatus.MISMATCH,
            reason="The mandatory Government Warning was not visible.",
        )
    if observation.visibility == Visibility.UNCERTAIN:
        return _warning_result(
            observation,
            status=CheckStatus.NEEDS_REVIEW,
            reason="It is uncertain whether the complete Government Warning is visible.",
        )
    if observation.readability != Readability.READABLE:
        return _warning_result(
            observation,
            status=CheckStatus.NEEDS_REVIEW,
            reason="The Government Warning was not clearly readable.",
        )
    if observation.text is None or not observation.text.strip():
        return _warning_result(
            observation,
            status=CheckStatus.MISMATCH,
            reason="The mandatory Government Warning text was missing.",
        )
    if normalize_warning_text(observation.text) != normalize_warning_text(GOVERNMENT_WARNING_TEXT):
        return _warning_result(
            observation,
            status=CheckStatus.MISMATCH,
            reason="The visible Government Warning wording differs from the required text.",
        )
    if observation.heading_text is None:
        return _warning_result(
            observation,
            status=CheckStatus.NEEDS_REVIEW,
            reason="The warning heading capitalization could not be evaluated.",
        )
    if normalize_warning_heading(observation.heading_text) != GOVERNMENT_WARNING_HEADING:
        return _warning_result(
            observation,
            status=CheckStatus.MISMATCH,
            reason="The Government Warning heading was not the required uppercase text.",
        )
    if (
        observation.heading_weight == TextWeight.UNCERTAIN
        or observation.body_weight == TextWeight.UNCERTAIN
    ):
        return _warning_result(
            observation,
            status=CheckStatus.NEEDS_REVIEW,
            reason="The warning heading or body text weight could not be determined.",
        )
    if observation.heading_weight != TextWeight.BOLD:
        return _warning_result(
            observation,
            status=CheckStatus.MISMATCH,
            reason="The Government Warning heading was visibly not bold.",
        )
    if observation.body_weight != TextWeight.NOT_BOLD:
        return _warning_result(
            observation,
            status=CheckStatus.MISMATCH,
            reason="The Government Warning body was visibly bold.",
        )
    return _warning_result(
        observation,
        status=CheckStatus.MATCH,
        reason="Required warning wording and observable heading/body styles match.",
    )


def derive_overall_outcome(checks: list[CheckResult]) -> OverallOutcome:
    names = [check.name for check in checks]
    if len(names) != len(CheckName) or set(names) != set(CheckName):
        raise ValueError("overall outcome requires exactly one of each check")
    if all(check.status == CheckStatus.MATCH for check in checks):
        return OverallOutcome.ALL_CHECKS_PASSED
    return OverallOutcome.NEEDS_REVIEW


def compare_review(
    expected: ExpectedReview,
    observations: ExtractionObservations,
    *,
    processing_duration_ms: int,
) -> ReviewResult:
    checks = [
        compare_brand(expected.brand_name, observations.brand_name),
        compare_class_type(expected.class_type, observations.class_type),
        compare_alcohol_content(expected.abv, observations.alcohol_content),
        compare_net_contents(expected.net_contents, observations.net_contents),
        compare_government_warning(observations.government_warning),
    ]
    return ReviewResult(
        outcome=derive_overall_outcome(checks),
        checks=checks,
        processing_duration_ms=processing_duration_ms,
    )


def unable_to_process_result(*, reason: str, processing_duration_ms: int) -> ReviewResult:
    checks = [
        CheckResult(name=name, status=CheckStatus.NOT_EVALUATED, reason=reason)
        for name in CheckName
    ]
    return ReviewResult(
        outcome=OverallOutcome.UNABLE_TO_PROCESS,
        checks=checks,
        processing_duration_ms=processing_duration_ms,
    )
