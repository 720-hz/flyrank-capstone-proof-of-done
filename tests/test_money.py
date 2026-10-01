"""Integer-only cost math for the LLM cost log — no floats on the money path,
same discipline as the usage-metering capstone."""
from app.lib.money import (
    ANTHROPIC_INPUT_MICROCENTS_PER_TOKEN,
    ANTHROPIC_OUTPUT_MICROCENTS_PER_TOKEN,
    compute_cost_micro_cents,
    format_usd,
)


def test_compute_cost_micro_cents_is_pure_integer_math():
    cost = compute_cost_micro_cents(1000, 500)
    assert cost == 1000 * ANTHROPIC_INPUT_MICROCENTS_PER_TOKEN + 500 * ANTHROPIC_OUTPUT_MICROCENTS_PER_TOKEN
    assert isinstance(cost, int)


def test_compute_cost_micro_cents_zero_usage_is_zero_cost():
    assert compute_cost_micro_cents(0, 0) == 0


def test_output_tokens_cost_more_per_token_than_input_tokens():
    # a documented pricing assumption (Haiku-class): verify the two constants
    # actually encode "output costs more", since a swapped assignment would
    # silently under-price every review.
    assert ANTHROPIC_OUTPUT_MICROCENTS_PER_TOKEN > ANTHROPIC_INPUT_MICROCENTS_PER_TOKEN


def test_format_usd_whole_dollars():
    # 1,000,000 micro-cents = 1 whole cent = $0.01... actually check the exact
    # scale: 100 whole cents = $1.00, and whole_cents = micro_cents // 1_000_000
    assert format_usd(100 * 1_000_000) == "$1.00"


def test_format_usd_cents_only():
    assert format_usd(5 * 1_000_000) == "$0.05"


def test_format_usd_zero():
    assert format_usd(0) == "$0.00"


def test_format_usd_shows_sub_cent_remainder_rather_than_rounding_silently():
    result = format_usd(500_500)  # less than one whole cent (1_000_000 micro-cents)
    assert result.startswith("$0.00")
    assert "sub-cent" in result
    assert "500500" in result


def test_format_usd_realistic_review_cost():
    # ~1200 input tokens + ~80 output tokens, a plausible single review call
    cost = compute_cost_micro_cents(1200, 80)
    formatted = format_usd(cost)
    assert formatted.startswith("$")
