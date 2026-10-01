"""Integer-only cost math for the LLM cost log — same micro-cents discipline
as the usage-metering capstone (no floats on the money path). These per-token
rates are a documented approximation, not pulled live from Anthropic — check
https://www.anthropic.com/pricing for current numbers and update the two
constants below if they've drifted since this was written."""
ANTHROPIC_INPUT_MICROCENTS_PER_TOKEN = 80   # ~$0.80 / 1M tokens (Haiku-class, approx)
ANTHROPIC_OUTPUT_MICROCENTS_PER_TOKEN = 400  # ~$4.00 / 1M tokens (Haiku-class, approx)


def compute_cost_micro_cents(input_tokens: int, output_tokens: int) -> int:
    return (
        input_tokens * ANTHROPIC_INPUT_MICROCENTS_PER_TOKEN
        + output_tokens * ANTHROPIC_OUTPUT_MICROCENTS_PER_TOKEN
    )


def format_usd(cost_micro_cents: int) -> str:
    whole_cents, sub_cent = divmod(cost_micro_cents, 1_000_000)
    dollars, cents = divmod(whole_cents, 100)
    base = f"${dollars}.{cents:02d}"
    if sub_cent:
        return f"{base} (+{sub_cent} sub-cent micro-cents)"
    return base
