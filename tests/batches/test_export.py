import pytest

from app.batches import CSV_EXPORT_COLUMNS, neutralize_spreadsheet_formula


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_like_csv_values_are_neutralized(prefix: str) -> None:
    value = f"{prefix}unsafe"

    assert neutralize_spreadsheet_formula(value) == f"'{value}"


def test_ordinary_csv_values_are_unchanged() -> None:
    assert neutralize_spreadsheet_formula("OLD TOM DISTILLERY") == "OLD TOM DISTILLERY"


def test_csv_contract_uses_status_not_full_warning_text() -> None:
    assert "Government Warning Status" in CSV_EXPORT_COLUMNS
    assert "Government Warning Text" not in CSV_EXPORT_COLUMNS
