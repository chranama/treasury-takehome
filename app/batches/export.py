"""Safe, deterministic CSV export for selected batch-review cases."""

import csv
from dataclasses import dataclass
from io import StringIO

from app.batches.contracts import BatchCaseState, BatchExpectedInput
from app.comparison import CheckName, ReviewResult

CSV_EXPORT_COLUMNS = (
    "Application ID",
    "Processing Status",
    "Overall Outcome",
    "Processing Duration (ms)",
    "Brand Name Status",
    "Class/Type Status",
    "Alcohol Content Status",
    "Net Contents Status",
    "Government Warning Status",
    "Expected Brand",
    "Extracted Brand",
    "Expected Class/Type",
    "Extracted Class/Type",
    "Expected ABV",
    "Extracted Alcohol Content",
    "Expected Net Contents",
    "Extracted Net Contents",
    "Short Reason",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_EXTRACTED_SEPARATOR = " | "


@dataclass(frozen=True, slots=True)
class BatchExportCase:
    application_id: str
    state: BatchCaseState
    expected_input: BatchExpectedInput
    processing_duration_ms: int | None = None
    result: ReviewResult | None = None
    short_reason: str | None = None

    def __post_init__(self) -> None:
        unselected_states = {
            BatchCaseState.NEEDS_CORRECTION,
            BatchCaseState.READY,
            BatchCaseState.NOT_SELECTED,
        }
        if self.state in unselected_states:
            raise ValueError("CSV export accepts selected cases only")
        if (self.state == BatchCaseState.COMPLETED) != (self.result is not None):
            raise ValueError("only completed export cases include a comparison result")


def neutralize_spreadsheet_formula(value: str) -> str:
    """Prefix formula-like user or model text so spreadsheet apps treat it as data."""

    if value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def build_results_csv(cases: list[BatchExportCase]) -> bytes:
    """Encode one selected case per row as UTF-8 with a spreadsheet-friendly BOM."""

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_EXPORT_COLUMNS)
    for case in cases:
        writer.writerow(_neutralized_export_row(case))
    return output.getvalue().encode("utf-8-sig")


def completed_short_reason(result: ReviewResult) -> str:
    """Return one bounded deterministic reason suitable for polling and export."""

    if all(check.status.value == "match" for check in result.checks):
        return "All five checks matched."
    check = next((item for item in result.checks if item.status.value != "match"), result.checks[0])
    return check.reason[:300]


def _neutralized_export_row(case: BatchExportCase) -> list[str]:
    checks = {check.name: check for check in case.result.checks} if case.result else {}

    def status(name: CheckName) -> str:
        check = checks.get(name)
        return check.status.value if check is not None else ""

    def extracted(name: CheckName) -> str:
        check = checks.get(name)
        return _EXTRACTED_SEPARATOR.join(check.extracted_values) if check is not None else ""

    values = [
        case.application_id,
        case.state.value,
        case.result.outcome.value if case.result else "",
        str(case.processing_duration_ms) if case.processing_duration_ms is not None else "",
        status(CheckName.BRAND_NAME),
        status(CheckName.CLASS_TYPE),
        status(CheckName.ALCOHOL_CONTENT),
        status(CheckName.NET_CONTENTS),
        status(CheckName.GOVERNMENT_WARNING),
        case.expected_input.brand_name,
        extracted(CheckName.BRAND_NAME),
        case.expected_input.class_type,
        extracted(CheckName.CLASS_TYPE),
        case.expected_input.expected_abv,
        extracted(CheckName.ALCOHOL_CONTENT),
        case.expected_input.expected_net_contents,
        extracted(CheckName.NET_CONTENTS),
        case.short_reason or (completed_short_reason(case.result) if case.result else ""),
    ]
    return [neutralize_spreadsheet_formula(value) for value in values]
