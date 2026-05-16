"""Input-token pricing per model family, in USD per million tokens.

Used by the cache view's "estimated dollars saved" calculation:
`cache_read_tokens × input_price / 1M` — the cost the user would have
paid if those tokens had been re-sent as fresh input instead of served
from cache.

This table lives in source (not on the wire) because ccusage does not
expose Anthropic's per-component pricing — only the resulting per-model
`cost`. Prices are taken from Anthropic's public pricing as of
2026-05-16; bump a number when prices move.
"""

from __future__ import annotations

from tokenscope.analytics import model_family

INPUT_PRICE_USD_PER_MTOK_BY_FAMILY: dict[str, float] = {
    "opus": 15.0,
    "sonnet": 3.0,
    "haiku": 1.0,
}

DEFAULT_INPUT_PRICE_USD_PER_MTOK = 3.0  # conservative middle estimate


def input_price_per_mtok(model_name: str) -> float:
    """USD per 1M input tokens for `model_name`, by family lookup.

    Unknown families fall back to `DEFAULT_INPUT_PRICE_USD_PER_MTOK`
    rather than raising — the dashboard should still display *something*
    when Anthropic ships a new family before we update this table.
    """
    family = model_family(model_name)
    return INPUT_PRICE_USD_PER_MTOK_BY_FAMILY.get(
        family, DEFAULT_INPUT_PRICE_USD_PER_MTOK
    )
