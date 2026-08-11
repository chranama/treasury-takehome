from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.comparison import (
    ApplicationErrorCategory,
    CheckName,
    CheckResult,
    CheckStatus,
    ExpectedNetContents,
    ExpectedReview,
    ExtractionObservations,
    NetContentsUnit,
    OverallOutcome,
    ReviewResult,
    SafeApplicationError,
    TextCandidate,
)


def expected_review(**overrides: object) -> ExpectedReview:
    values: dict[str, object] = {
        "brand_name": "OLD TOM DISTILLERY",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "abv": Decimal("45"),
        "net_contents": ExpectedNetContents(
            value=Decimal("750"),
            unit=NetContentsUnit.MILLILITER,
        ),
    }
    values.update(overrides)
    return ExpectedReview(**values)


def test_expected_review_strips_outer_text_whitespace() -> None:
    review = expected_review(brand_name="  OLD TOM DISTILLERY  ")

    assert review.brand_name == "OLD TOM DISTILLERY"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("brand_name", "   "),
        ("class_type", "\n\t"),
        ("abv", Decimal("-0.1")),
        ("abv", Decimal("100.1")),
    ],
)
def test_expected_review_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        expected_review(**{field: value})


def test_expected_net_contents_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ExpectedNetContents(value=Decimal("0"), unit=NetContentsUnit.MILLILITER)


def test_text_candidate_preserves_nonblank_raw_text() -> None:
    candidate = TextCandidate(text="  Stone’s   Throw  ")

    assert candidate.text == "  Stone’s   Throw  "


def test_text_candidate_rejects_blank_text() -> None:
    with pytest.raises(ValidationError):
        TextCandidate(text=" \n ")


def test_extraction_schema_contains_observations_not_expected_values() -> None:
    assert set(ExtractionObservations.model_fields) == {
        "brand_name",
        "class_type",
        "alcohol_content",
        "net_contents",
        "government_warning",
        "note",
    }


def test_review_result_requires_exactly_one_of_each_check() -> None:
    duplicate_checks = [
        CheckResult(
            name=CheckName.BRAND_NAME,
            status=CheckStatus.NOT_EVALUATED,
            reason="Not evaluated.",
        )
        for _ in CheckName
    ]

    with pytest.raises(ValidationError):
        ReviewResult(
            outcome=OverallOutcome.UNABLE_TO_PROCESS,
            checks=duplicate_checks,
            processing_duration_ms=0,
        )


def test_check_result_requires_a_nonblank_reason() -> None:
    with pytest.raises(ValidationError):
        CheckResult(name=CheckName.BRAND_NAME, status=CheckStatus.MATCH, reason=" ")


def test_review_result_rejects_an_outcome_that_contradicts_check_statuses() -> None:
    matching_checks = [
        CheckResult(name=name, status=CheckStatus.MATCH, reason="Match.") for name in CheckName
    ]

    with pytest.raises(ValidationError):
        ReviewResult(
            outcome=OverallOutcome.NEEDS_REVIEW,
            checks=matching_checks,
            processing_duration_ms=0,
        )


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (OverallOutcome.ALL_CHECKS_PASSED, CheckStatus.NEEDS_REVIEW),
        (OverallOutcome.UNABLE_TO_PROCESS, CheckStatus.MATCH),
        (OverallOutcome.NEEDS_REVIEW, CheckStatus.NOT_EVALUATED),
    ],
)
def test_review_result_rejects_other_inconsistent_outcome_combinations(
    outcome: OverallOutcome,
    status: CheckStatus,
) -> None:
    checks = [CheckResult(name=name, status=status, reason="Result.") for name in CheckName]

    with pytest.raises(ValidationError):
        ReviewResult(outcome=outcome, checks=checks, processing_duration_ms=0)


def test_safe_application_error_requires_a_nonblank_public_message() -> None:
    error = SafeApplicationError(
        category=ApplicationErrorCategory.INVALID_INPUT,
        message="  The request was invalid.  ",
        correlation_id="request-123",
    )

    assert error.message == "The request was invalid."
    with pytest.raises(ValidationError):
        SafeApplicationError(
            category=ApplicationErrorCategory.INTERNAL_ERROR,
            message=" ",
        )
