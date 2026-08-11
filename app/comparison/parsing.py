import re
from dataclasses import dataclass
from decimal import Decimal

from app.comparison.normalization import ordered_unique

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_ABV_PATTERN = re.compile(rf"(?P<value>{_NUMBER})\s*(?:%|percent\b)", re.IGNORECASE)
_PROOF_PATTERN = re.compile(rf"(?P<value>{_NUMBER})\s*(?:°\s*)?proof\b", re.IGNORECASE)
_NET_CONTENTS_PATTERN = re.compile(
    rf"(?P<value>{_NUMBER})\s*"
    r"(?P<unit>mL|milliliters?|millilitres?|L|liters?|litres?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AlcoholParseResult:
    abv_values: tuple[Decimal, ...]
    used_proof: bool
    has_unrecognized_statement: bool
    has_out_of_range_value: bool

    @property
    def has_conflict(self) -> bool:
        return len(self.abv_values) > 1


@dataclass(frozen=True)
class NetContentsParseResult:
    milliliter_values: tuple[Decimal, ...]
    has_unrecognized_statement: bool
    has_nonpositive_value: bool

    @property
    def has_conflict(self) -> bool:
        return len(self.milliliter_values) > 1


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def parse_alcohol_statements(statements: list[str]) -> AlcoholParseResult:
    abv_values: list[Decimal] = []
    used_proof = False
    has_unrecognized = False
    has_out_of_range = False

    for statement in statements:
        statement_values: list[Decimal] = []
        for match in _ABV_PATTERN.finditer(statement):
            value = _decimal(match.group("value"))
            if Decimal(0) <= value <= Decimal(100):
                statement_values.append(value)
            else:
                has_out_of_range = True

        for match in _PROOF_PATTERN.finditer(statement):
            used_proof = True
            proof = _decimal(match.group("value"))
            if Decimal(0) <= proof <= Decimal(200):
                statement_values.append(proof / Decimal(2))
            else:
                has_out_of_range = True

        if not statement_values and not has_out_of_range:
            has_unrecognized = True
        abv_values.extend(statement_values)

    return AlcoholParseResult(
        abv_values=tuple(ordered_unique(abv_values)),
        used_proof=used_proof,
        has_unrecognized_statement=has_unrecognized,
        has_out_of_range_value=has_out_of_range,
    )


def parse_net_contents_statements(statements: list[str]) -> NetContentsParseResult:
    milliliter_values: list[Decimal] = []
    has_unrecognized = False
    has_nonpositive = False

    for statement in statements:
        matches = list(_NET_CONTENTS_PATTERN.finditer(statement))
        if not matches:
            has_unrecognized = True
            continue

        for match in matches:
            value = _decimal(match.group("value"))
            if value <= 0:
                has_nonpositive = True
                continue

            unit = match.group("unit").casefold()
            multiplier = (
                Decimal(1000) if unit in {"l", "liter", "liters", "litre", "litres"} else Decimal(1)
            )
            milliliter_values.append(value * multiplier)

    return NetContentsParseResult(
        milliliter_values=tuple(ordered_unique(milliliter_values)),
        has_unrecognized_statement=has_unrecognized,
        has_nonpositive_value=has_nonpositive,
    )
