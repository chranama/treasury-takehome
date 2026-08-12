"""Safe CSV export contract shared by later P1 milestones."""

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


def neutralize_spreadsheet_formula(value: str) -> str:
    """Prefix formula-like user or model text so spreadsheet apps treat it as data."""

    if value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value
