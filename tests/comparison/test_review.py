from decimal import Decimal

import pytest

from app.comparison import (
    GOVERNMENT_WARNING_TEXT,
    CheckName,
    CheckStatus,
    ExpectedNetContents,
    ExpectedReview,
    ExtractionObservations,
    FieldObservation,
    NetContentsUnit,
    OverallOutcome,
    Readability,
    TextCandidate,
    TextWeight,
    Visibility,
    WarningObservation,
    compare_alcohol_content,
    compare_brand,
    compare_class_type,
    compare_government_warning,
    compare_net_contents,
    compare_review,
    derive_overall_outcome,
    unable_to_process_result,
)


def field(
    *values: str,
    visibility: Visibility = Visibility.VISIBLE,
    readability: Readability = Readability.READABLE,
) -> FieldObservation:
    return FieldObservation(
        candidates=[TextCandidate(text=value) for value in values],
        visibility=visibility,
        readability=readability,
    )


def warning(**overrides: object) -> WarningObservation:
    values: dict[str, object] = {
        "text": GOVERNMENT_WARNING_TEXT,
        "heading_text": "GOVERNMENT WARNING:",
        "heading_weight": TextWeight.BOLD,
        "body_weight": TextWeight.NOT_BOLD,
        "visibility": Visibility.VISIBLE,
        "readability": Readability.READABLE,
    }
    values.update(overrides)
    return WarningObservation(**values)


def expected_review() -> ExpectedReview:
    return ExpectedReview(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        abv=Decimal("45"),
        net_contents=ExpectedNetContents(
            value=Decimal("750"),
            unit=NetContentsUnit.MILLILITER,
        ),
    )


def matching_observations(**overrides: object) -> ExtractionObservations:
    values: dict[str, object] = {
        "brand_name": field("Old Tom Distillery"),
        "class_type": field("Kentucky Straight Bourbon Whiskey"),
        "alcohol_content": field("90 Proof"),
        "net_contents": field("0.75 L"),
        "government_warning": warning(),
    }
    values.update(overrides)
    return ExtractionObservations(**values)


def test_clear_fixture_passes_all_five_checks() -> None:
    result = compare_review(
        expected_review(),
        matching_observations(),
        processing_duration_ms=125,
    )

    assert result.outcome == OverallOutcome.ALL_CHECKS_PASSED
    assert [check.name for check in result.checks] == list(CheckName)
    assert all(check.status == CheckStatus.MATCH for check in result.checks)
    assert result.processing_duration_ms == 125


def test_brand_variation_preserves_raw_values_and_matches() -> None:
    result = compare_brand("STONE'S THROW", field("  Stone’s   Throw  "))

    assert result.status == CheckStatus.MATCH
    assert result.expected_value == "STONE'S THROW"
    assert result.extracted_values == ["  Stone’s   Throw  "]
    assert result.normalized_expected == "stone's throw"
    assert result.normalized_extracted == ["stone's throw"]


def test_material_brand_difference_needs_review() -> None:
    result = compare_brand("Stone's Throw", field("Stone Throw"))

    assert result.status == CheckStatus.NEEDS_REVIEW


def test_material_or_reordered_class_type_difference_needs_review() -> None:
    result = compare_class_type("Bourbon Whiskey", field("Whiskey Bourbon"))

    assert result.status == CheckStatus.NEEDS_REVIEW


def test_equivalent_duplicate_text_candidates_can_match() -> None:
    result = compare_brand("Stone's Throw", field("STONE'S THROW", "Stone’s   Throw"))

    assert result.status == CheckStatus.MATCH


def test_conflicting_text_candidates_need_review_even_if_one_matches() -> None:
    result = compare_brand("Stone's Throw", field("Stone's Throw", "Stone Throw"))

    assert result.status == CheckStatus.NEEDS_REVIEW


@pytest.mark.parametrize("statement", ["45% Alc./Vol.", "90 Proof"])
def test_equivalent_abv_forms_match(statement: str) -> None:
    result = compare_alcohol_content(Decimal("45"), field(statement))

    assert result.status == CheckStatus.MATCH
    assert result.normalized_extracted == ["45% ABV"]


def test_proof_conversion_is_explained() -> None:
    result = compare_alcohol_content(Decimal("45"), field("90 Proof"))

    assert result.status == CheckStatus.MATCH
    assert "proof / 2" in result.reason


def test_conflicting_abv_and_proof_need_review() -> None:
    result = compare_alcohol_content(Decimal("45"), field("45% Alc./Vol. 100 Proof"))

    assert result.status == CheckStatus.NEEDS_REVIEW
    assert result.normalized_extracted == ["45% ABV", "50% ABV"]


def test_different_abv_is_a_mismatch_without_tolerance() -> None:
    result = compare_alcohol_content(Decimal("45"), field("45.01% Alc./Vol."))

    assert result.status == CheckStatus.MISMATCH


def test_unrecognized_alcohol_statement_needs_review() -> None:
    result = compare_alcohol_content(Decimal("45"), field("alcohol statement unclear"))

    assert result.status == CheckStatus.NEEDS_REVIEW
    assert result.normalized_extracted == []


def test_missing_alcohol_observation_needs_review_without_parsing() -> None:
    result = compare_alcohol_content(
        Decimal("45"),
        field(visibility=Visibility.NOT_VISIBLE),
    )

    assert result.status == CheckStatus.NEEDS_REVIEW


@pytest.mark.parametrize("statement", ["750 mL", "750 ml", "0.75 L"])
def test_equivalent_net_contents_match(statement: str) -> None:
    expected = ExpectedNetContents(value=Decimal("750"), unit=NetContentsUnit.MILLILITER)

    result = compare_net_contents(expected, field(statement))

    assert result.status == CheckStatus.MATCH
    assert result.normalized_extracted == ["750 mL"]


def test_expected_liters_normalize_to_milliliters() -> None:
    expected = ExpectedNetContents(value=Decimal("0.75"), unit=NetContentsUnit.LITER)

    result = compare_net_contents(expected, field("750 mL"))

    assert result.status == CheckStatus.MATCH
    assert result.expected_value == "0.75 L"
    assert result.normalized_expected == "750 mL"


def test_different_net_contents_are_a_mismatch() -> None:
    expected = ExpectedNetContents(value=Decimal("750"), unit=NetContentsUnit.MILLILITER)

    result = compare_net_contents(expected, field("700 mL"))

    assert result.status == CheckStatus.MISMATCH


def test_unrecognized_or_conflicting_net_contents_need_review() -> None:
    expected = ExpectedNetContents(value=Decimal("750"), unit=NetContentsUnit.MILLILITER)

    unrecognized = compare_net_contents(expected, field("25 fl oz"))
    conflicting = compare_net_contents(expected, field("750 mL", "700 mL"))

    assert unrecognized.status == CheckStatus.NEEDS_REVIEW
    assert conflicting.status == CheckStatus.NEEDS_REVIEW


def test_missing_net_contents_observation_needs_review_without_parsing() -> None:
    expected = ExpectedNetContents(value=Decimal("750"), unit=NetContentsUnit.MILLILITER)

    result = compare_net_contents(expected, field(visibility=Visibility.NOT_VISIBLE))

    assert result.status == CheckStatus.NEEDS_REVIEW


def test_warning_whitespace_and_line_breaks_do_not_change_wording_match() -> None:
    line_broken = GOVERNMENT_WARNING_TEXT.replace(" (2) ", "\n\n(2)   ")

    result = compare_government_warning(warning(text=line_broken))

    assert result.status == CheckStatus.MATCH
    assert len(result.limitations) == 3


def test_missing_warning_is_a_mismatch() -> None:
    result = compare_government_warning(
        warning(
            text=None,
            heading_text=None,
            visibility=Visibility.NOT_VISIBLE,
            readability=Readability.UNREADABLE,
        )
    )

    assert result.status == CheckStatus.MISMATCH


def test_visible_warning_with_missing_text_is_a_mismatch() -> None:
    result = compare_government_warning(warning(text=None))

    assert result.status == CheckStatus.MISMATCH


def test_uncertain_warning_visibility_needs_review() -> None:
    result = compare_government_warning(warning(visibility=Visibility.UNCERTAIN))

    assert result.status == CheckStatus.NEEDS_REVIEW


def test_altered_warning_wording_is_a_mismatch() -> None:
    altered = GOVERNMENT_WARNING_TEXT.replace("birth defects", "harm")

    result = compare_government_warning(warning(text=altered))

    assert result.status == CheckStatus.MISMATCH


def test_warning_heading_must_be_uppercase() -> None:
    result = compare_government_warning(warning(heading_text="Government Warning:"))

    assert result.status == CheckStatus.MISMATCH


def test_missing_warning_heading_observation_needs_review() -> None:
    result = compare_government_warning(warning(heading_text=None))

    assert result.status == CheckStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "overrides",
    [
        {"heading_weight": TextWeight.UNCERTAIN},
        {"body_weight": TextWeight.UNCERTAIN},
        {"readability": Readability.PARTIALLY_READABLE},
    ],
)
def test_uncertain_warning_style_or_readability_needs_review(
    overrides: dict[str, object],
) -> None:
    result = compare_government_warning(warning(**overrides))

    assert result.status == CheckStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "overrides",
    [
        {"heading_weight": TextWeight.NOT_BOLD},
        {"body_weight": TextWeight.BOLD},
    ],
)
def test_determinable_wrong_warning_style_is_a_mismatch(
    overrides: dict[str, object],
) -> None:
    result = compare_government_warning(warning(**overrides))

    assert result.status == CheckStatus.MISMATCH


@pytest.mark.parametrize(
    "observation",
    [
        field(visibility=Visibility.NOT_VISIBLE),
        field("unclear", readability=Readability.UNREADABLE),
        field(visibility=Visibility.UNCERTAIN),
    ],
)
def test_missing_unreadable_or_uncertain_regular_fields_need_review(
    observation: FieldObservation,
) -> None:
    result = compare_brand("Expected Brand", observation)

    assert result.status == CheckStatus.NEEDS_REVIEW
    assert result.status != CheckStatus.MATCH


def test_visible_readable_field_without_candidates_needs_review() -> None:
    result = compare_brand("Expected Brand", field())

    assert result.status == CheckStatus.NEEDS_REVIEW


def test_any_nonmatch_routes_the_overall_review_to_needs_review() -> None:
    result = compare_review(
        expected_review(),
        matching_observations(brand_name=field("Different Brand")),
        processing_duration_ms=10,
    )

    assert result.outcome == OverallOutcome.NEEDS_REVIEW


def test_overall_aggregation_rejects_a_partial_check_set() -> None:
    with pytest.raises(ValueError, match="exactly one of each check"):
        derive_overall_outcome([compare_brand("Brand", field("Brand"))])


def test_unable_to_process_marks_all_checks_not_evaluated() -> None:
    result = unable_to_process_result(
        reason="The image could not be decoded.",
        processing_duration_ms=4,
    )

    assert result.outcome == OverallOutcome.UNABLE_TO_PROCESS
    assert len(result.checks) == 5
    assert all(check.status == CheckStatus.NOT_EVALUATED for check in result.checks)
