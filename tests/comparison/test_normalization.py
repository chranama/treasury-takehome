from decimal import Decimal

from app.comparison.normalization import (
    format_decimal,
    normalize_brand,
    normalize_class_type,
    normalize_warning_heading,
    normalize_warning_text,
)


def test_brand_normalization_handles_case_whitespace_and_typographic_apostrophes() -> None:
    assert normalize_brand("  STONE'S   THROW ") == normalize_brand("Stone’s Throw")


def test_brand_normalization_does_not_ignore_missing_or_reordered_words() -> None:
    assert normalize_brand("Stone Throw") != normalize_brand("Stone's Throw")
    assert normalize_brand("Throw Stone's") != normalize_brand("Stone's Throw")


def test_class_type_normalization_is_conservative() -> None:
    assert normalize_class_type("  Kentucky  Straight Bourbon ") == (
        normalize_class_type("kentucky straight bourbon")
    )
    assert normalize_class_type("Bourbon Whiskey") != normalize_class_type("Whiskey Bourbon")


def test_warning_normalization_changes_only_unicode_and_whitespace_layout() -> None:
    assert normalize_warning_text("GOVERNMENT WARNING:\n  (1) Text") == (
        "GOVERNMENT WARNING: (1) Text"
    )
    assert normalize_warning_text("Government Warning: (1) Text") != (
        normalize_warning_text("GOVERNMENT WARNING: (1) Text")
    )


def test_warning_heading_allows_the_separator_colon_but_preserves_case() -> None:
    assert normalize_warning_heading(" GOVERNMENT   WARNING: ") == "GOVERNMENT WARNING"
    assert normalize_warning_heading("Government Warning:") != "GOVERNMENT WARNING"


def test_decimal_formatting_avoids_exponents_and_insignificant_zeroes() -> None:
    assert format_decimal(Decimal("45.00")) == "45"
    assert format_decimal(Decimal("0.750")) == "0.75"
    assert format_decimal(Decimal("1000")) == "1000"
