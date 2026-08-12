import csv
from io import StringIO

import pytest

from app.batches import (
    CSV_EXPORT_COLUMNS,
    BatchCaseState,
    BatchExpectedInput,
    BatchExportCase,
    build_results_csv,
    neutralize_spreadsheet_formula,
)
from app.comparison import CheckName, CheckResult, CheckStatus, OverallOutcome, ReviewResult


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_like_csv_values_are_neutralized(prefix: str) -> None:
    value = f"{prefix}unsafe"

    assert neutralize_spreadsheet_formula(value) == f"'{value}"


def test_ordinary_csv_values_are_unchanged() -> None:
    assert neutralize_spreadsheet_formula("OLD TOM DISTILLERY") == "OLD TOM DISTILLERY"


def test_csv_contract_uses_status_not_full_warning_text() -> None:
    assert "Government Warning Status" in CSV_EXPORT_COLUMNS
    assert "Government Warning Text" not in CSV_EXPORT_COLUMNS


def test_results_csv_is_utf8_bom_encoded_quoted_and_formula_neutralized() -> None:
    result = ReviewResult(
        outcome=OverallOutcome.ALL_CHECKS_PASSED,
        processing_duration_ms=12,
        checks=[
            CheckResult(
                name=name,
                status=CheckStatus.MATCH,
                expected_value="expected",
                extracted_values=[f"={name.value}", "value,with comma", "line\nbreak"],
                reason="Matched.",
            )
            for name in CheckName
        ],
    )
    content = build_results_csv(
        [
            BatchExportCase(
                application_id="+APP-1",
                state=BatchCaseState.COMPLETED,
                expected_input=BatchExpectedInput(
                    brand_name="@BRAND",
                    class_type="-CLASS",
                    expected_abv="=45",
                    expected_net_contents="\t750 mL",
                ),
                processing_duration_ms=12,
                result=result,
            )
        ]
    )

    assert content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(StringIO(content.decode("utf-8-sig"))))
    assert len(rows) == 1
    row = rows[0]
    assert row["Application ID"] == "'+APP-1"
    assert row["Expected Brand"] == "'@BRAND"
    assert row["Expected Class/Type"] == "'-CLASS"
    assert row["Expected ABV"] == "'=45"
    assert row["Expected Net Contents"] == "'\t750 mL"
    assert row["Extracted Brand"].startswith("'=brand_name")
    assert "value,with comma" in row["Extracted Brand"]
    assert "line\nbreak" in row["Extracted Brand"]
    assert row["Short Reason"] == "All five checks matched."


def test_export_projection_rejects_unselected_cases() -> None:
    with pytest.raises(ValueError, match="selected cases only"):
        BatchExportCase(
            application_id="APP-1",
            state=BatchCaseState.NOT_SELECTED,
            expected_input=BatchExpectedInput(
                brand_name="Brand",
                class_type="Bourbon",
                expected_abv="45",
                expected_net_contents="750 mL",
            ),
        )
