from decimal import Decimal

import pytest

from app.comparison.parsing import parse_alcohol_statements, parse_net_contents_statements


@pytest.mark.parametrize(
    "statement",
    ["45%", "45% Alc./Vol.", "ALC. 45% BY VOL.", "45 percent alcohol by volume"],
)
def test_common_abv_forms_parse_to_the_same_value(statement: str) -> None:
    result = parse_alcohol_statements([statement])

    assert result.abv_values == (Decimal("45"),)
    assert not result.has_conflict


def test_us_proof_converts_to_abv() -> None:
    result = parse_alcohol_statements(["90 Proof"])

    assert result.abv_values == (Decimal("45"),)
    assert result.used_proof


def test_equivalent_abv_and_proof_are_not_a_conflict() -> None:
    result = parse_alcohol_statements(["45% Alc./Vol. 90 Proof"])

    assert result.abv_values == (Decimal("45"),)
    assert not result.has_conflict


def test_conflicting_abv_and_proof_are_preserved() -> None:
    result = parse_alcohol_statements(["45% Alc./Vol. 100 Proof"])

    assert result.abv_values == (Decimal("45"), Decimal("50"))
    assert result.has_conflict


def test_unknown_and_out_of_range_alcohol_statements_are_not_invented() -> None:
    unknown = parse_alcohol_statements(["alcohol statement unclear"])
    out_of_range = parse_alcohol_statements(["201 Proof"])
    out_of_range_abv = parse_alcohol_statements(["101% Alc./Vol."])

    assert unknown.abv_values == ()
    assert unknown.has_unrecognized_statement
    assert out_of_range.abv_values == ()
    assert out_of_range.has_out_of_range_value
    assert out_of_range_abv.abv_values == ()
    assert out_of_range_abv.has_out_of_range_value


@pytest.mark.parametrize(
    ("statement", "milliliters"),
    [
        ("750 mL", Decimal("750")),
        ("750 ml", Decimal("750")),
        ("0.75 L", Decimal("750")),
        ("750 milliliters", Decimal("750")),
        ("1,000 mL", Decimal("1000")),
    ],
)
def test_common_metric_net_contents_parse_to_milliliters(
    statement: str,
    milliliters: Decimal,
) -> None:
    result = parse_net_contents_statements([statement])

    assert result.milliliter_values == (milliliters,)
    assert not result.has_unrecognized_statement


def test_unrecognized_nonmetric_unit_remains_unknown() -> None:
    result = parse_net_contents_statements(["25 fl oz"])

    assert result.milliliter_values == ()
    assert result.has_unrecognized_statement


def test_nonpositive_metric_quantity_is_not_accepted() -> None:
    result = parse_net_contents_statements(["0 mL"])

    assert result.milliliter_values == ()
    assert result.has_nonpositive_value


def test_conflicting_metric_quantities_are_preserved() -> None:
    result = parse_net_contents_statements(["750 mL", "700 mL"])

    assert result.milliliter_values == (Decimal("750"), Decimal("700"))
    assert result.has_conflict
