from decimal import Decimal
from typing import Protocol

PRICING_CHECKED_AT = "2026-08-11"
PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"
LUNA_INPUT_USD_PER_MILLION = Decimal("0.20")
LUNA_CACHED_INPUT_USD_PER_MILLION = Decimal("0.02")
LUNA_OUTPUT_USD_PER_MILLION = Decimal("1.20")
LUNA_FAST_RATE_MULTIPLIER = Decimal("2")
COST_QUANTUM_USD = Decimal("0.00000001")


class TokenUsage(Protocol):
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


def estimated_cost_usd(
    model: str,
    usage: TokenUsage | None,
    service_tier: str = "default",
) -> Decimal | None:
    """Estimate one completed response from versioned provider-reported token usage."""

    if (
        model != "gpt-5.6-luna"
        or usage is None
        or service_tier not in {"default", "fast", "priority"}
    ):
        return None
    multiplier = LUNA_FAST_RATE_MULTIPLIER if service_tier in {"fast", "priority"} else Decimal(1)
    uncached_tokens = max(0, usage.input_tokens - usage.cached_input_tokens)
    denominator = Decimal(1_000_000)
    cost = (
        Decimal(uncached_tokens) / denominator * LUNA_INPUT_USD_PER_MILLION * multiplier
        + Decimal(usage.cached_input_tokens)
        / denominator
        * LUNA_CACHED_INPUT_USD_PER_MILLION
        * multiplier
        + Decimal(usage.output_tokens) / denominator * LUNA_OUTPUT_USD_PER_MILLION * multiplier
    )
    return cost.quantize(COST_QUANTUM_USD)
