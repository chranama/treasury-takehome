import re
import unicodedata
from decimal import Decimal

_WHITESPACE = re.compile(r"\s+")
_APOSTROPHE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u02bc": "'",
        "\uff07": "'",
    }
)


def normalize_whitespace(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value).strip())


def normalize_brand(value: str) -> str:
    return normalize_whitespace(value.translate(_APOSTROPHE_TRANSLATION)).casefold()


def normalize_class_type(value: str) -> str:
    return normalize_whitespace(value).casefold()


def normalize_warning_text(value: str) -> str:
    return normalize_whitespace(value)


def normalize_warning_heading(value: str) -> str:
    return normalize_whitespace(value).removesuffix(":").rstrip()


def format_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def ordered_unique[T](values: list[T]) -> list[T]:
    result: list[T] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
