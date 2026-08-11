import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from app.comparison import (
    CheckName,
    CheckStatus,
    ExpectedNetContents,
    ExpectedReview,
    ExtractionObservations,
    NetContentsUnit,
    OverallOutcome,
    compare_review,
)
from app.extraction import (
    ExtractionError,
    ExtractionErrorKind,
    FakeExtractionAdapter,
    FakeExtractionFailure,
    FakeExtractionScenario,
    ImageMediaType,
    PreparedImage,
)


@pytest.fixture
def prepared_image(tmp_path: Path) -> PreparedImage:
    return PreparedImage(
        path=tmp_path / "prepared.png",
        media_type=ImageMediaType.PNG,
        width=1200,
        height=800,
        byte_count=4096,
    )


@pytest.fixture
def expected_review() -> ExpectedReview:
    return ExpectedReview(
        brand_name="Treasury Reserve",
        class_type="Kentucky Straight Bourbon Whiskey",
        abv=Decimal("45"),
        net_contents=ExpectedNetContents(
            value=Decimal("750"),
            unit=NetContentsUnit.MILLILITER,
        ),
    )


def extract(
    scenario: FakeExtractionScenario,
    prepared_image: PreparedImage,
) -> ExtractionObservations:
    adapter = FakeExtractionAdapter(scenario=scenario)
    return asyncio.run(adapter.extract(prepared_image))


@pytest.mark.parametrize("scenario", list(FakeExtractionScenario))
def test_every_named_scenario_returns_valid_observations(
    scenario: FakeExtractionScenario,
    prepared_image: PreparedImage,
) -> None:
    observations = extract(scenario, prepared_image)

    assert isinstance(observations, ExtractionObservations)


@pytest.mark.parametrize(
    "scenario",
    [
        FakeExtractionScenario.CLEAR_MATCHING_LABEL,
        FakeExtractionScenario.EQUIVALENT_PROOF_AND_ABV,
        FakeExtractionScenario.EQUIVALENT_NET_CONTENTS,
    ],
)
def test_matching_and_equivalent_scenarios_pass_all_checks(
    scenario: FakeExtractionScenario,
    prepared_image: PreparedImage,
    expected_review: ExpectedReview,
) -> None:
    result = compare_review(
        expected_review,
        extract(scenario, prepared_image),
        processing_duration_ms=0,
    )

    assert result.outcome == OverallOutcome.ALL_CHECKS_PASSED
    assert all(check.status == CheckStatus.MATCH for check in result.checks)


@pytest.mark.parametrize(
    ("scenario", "check_name", "check_status"),
    [
        (
            FakeExtractionScenario.BRAND_MISMATCH,
            CheckName.BRAND_NAME,
            CheckStatus.NEEDS_REVIEW,
        ),
        (
            FakeExtractionScenario.CLASS_TYPE_MISMATCH,
            CheckName.CLASS_TYPE,
            CheckStatus.NEEDS_REVIEW,
        ),
        (
            FakeExtractionScenario.CONFLICTING_PROOF_AND_ABV,
            CheckName.ALCOHOL_CONTENT,
            CheckStatus.NEEDS_REVIEW,
        ),
        (
            FakeExtractionScenario.MISMATCHED_NET_CONTENTS,
            CheckName.NET_CONTENTS,
            CheckStatus.MISMATCH,
        ),
        (
            FakeExtractionScenario.ALTERED_WARNING_TEXT,
            CheckName.GOVERNMENT_WARNING,
            CheckStatus.MISMATCH,
        ),
        (
            FakeExtractionScenario.MISSING_WARNING,
            CheckName.GOVERNMENT_WARNING,
            CheckStatus.MISMATCH,
        ),
        (
            FakeExtractionScenario.UNCERTAIN_WARNING_STYLE,
            CheckName.GOVERNMENT_WARNING,
            CheckStatus.NEEDS_REVIEW,
        ),
        (
            FakeExtractionScenario.AMBIGUOUS_CANDIDATES,
            CheckName.BRAND_NAME,
            CheckStatus.NEEDS_REVIEW,
        ),
    ],
)
def test_nonpassing_scenarios_target_the_expected_check(
    scenario: FakeExtractionScenario,
    check_name: CheckName,
    check_status: CheckStatus,
    prepared_image: PreparedImage,
    expected_review: ExpectedReview,
) -> None:
    result = compare_review(
        expected_review,
        extract(scenario, prepared_image),
        processing_duration_ms=0,
    )

    checks = {check.name: check for check in result.checks}
    assert result.outcome == OverallOutcome.NEEDS_REVIEW
    assert checks[check_name].status == check_status


def test_unreadable_scenario_preserves_unknowns(
    prepared_image: PreparedImage,
    expected_review: ExpectedReview,
) -> None:
    observations = extract(FakeExtractionScenario.UNREADABLE_IMAGE, prepared_image)
    result = compare_review(expected_review, observations, processing_duration_ms=0)

    assert observations.brand_name.candidates == []
    assert observations.class_type.candidates == []
    assert observations.alcohol_content.candidates == []
    assert observations.net_contents.candidates == []
    assert result.outcome == OverallOutcome.NEEDS_REVIEW
    assert all(check.status == CheckStatus.NEEDS_REVIEW for check in result.checks)


def test_fake_adapter_returns_fresh_observations(
    prepared_image: PreparedImage,
) -> None:
    first = extract(FakeExtractionScenario.CLEAR_MATCHING_LABEL, prepared_image)
    first.brand_name.candidates[0].text = "Mutated"

    second = extract(FakeExtractionScenario.CLEAR_MATCHING_LABEL, prepared_image)

    assert second.brand_name.candidates[0].text == "Treasury Reserve"


@pytest.mark.parametrize(
    ("failure", "expected_kind", "retryable"),
    [
        (FakeExtractionFailure.TIMEOUT, ExtractionErrorKind.TIMEOUT, True),
        (
            FakeExtractionFailure.MALFORMED_OUTPUT,
            ExtractionErrorKind.MALFORMED_OUTPUT,
            False,
        ),
        (
            FakeExtractionFailure.TRANSIENT_FAILURE,
            ExtractionErrorKind.TRANSIENT_FAILURE,
            True,
        ),
        (FakeExtractionFailure.UNAVAILABLE, ExtractionErrorKind.UNAVAILABLE, False),
        (
            FakeExtractionFailure.INTERNAL_FAILURE,
            ExtractionErrorKind.INTERNAL_FAILURE,
            False,
        ),
    ],
)
def test_fake_adapter_can_exercise_bounded_failures(
    failure: FakeExtractionFailure,
    expected_kind: ExtractionErrorKind,
    retryable: bool,
    prepared_image: PreparedImage,
) -> None:
    adapter = FakeExtractionAdapter(failure=failure)

    with pytest.raises(ExtractionError) as caught:
        asyncio.run(adapter.extract(prepared_image))

    assert caught.value.kind == expected_kind
    assert caught.value.retryable is retryable
    assert caught.value.safe_message == str(caught.value)
