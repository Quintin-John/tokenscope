"""Pure-function rollups over ccusage report shapes.

No Streamlit imports, no I/O — every function here is deterministic and
unit-testable in isolation. Anything that needs to shell out or cache
belongs in `tokenscope.ccusage` or `tokenscope.data`.

The functions accept the pydantic models from `tokenscope.models` (or
synthetic stand-ins with the same attributes) and return plain Python
values — the dashboard layer is responsible for shaping them into Plotly
figures.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Protocol

from tokenscope.models import BlocksReport, DailyReport


class _HasCacheTokens(Protocol):
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int


class _HasCacheReadOnly(Protocol):
    cache_read_tokens: int


def rolling_cost_average(
    daily_report: DailyReport, window_days: int
) -> list[tuple[str, float]]:
    """Compute a trailing-window mean of `total_cost` over the daily entries.

    Entries are sorted ascending by date before windowing. For day *i*, the
    window covers the most recent `window_days` entries up to and including
    day *i* — so early days in the report average over a shorter window
    rather than producing NaN.

    Returns `[(date, avg_cost), ...]` in ascending date order. Empty input
    yields an empty list. Raises `ValueError` for non-positive windows.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")
    entries = sorted(daily_report.daily, key=lambda e: e.date)
    out: list[tuple[str, float]] = []
    for i, entry in enumerate(entries):
        start = max(0, i - window_days + 1)
        window = entries[start : i + 1]
        avg = sum(e.total_cost for e in window) / len(window)
        out.append((entry.date, avg))
    return out


def cache_hit_ratio(entry: _HasCacheTokens) -> float:
    """Fraction of "cache-eligible" tokens that were served by a cache read.

    Formula: `cache_read / (input + cache_create + cache_read)`. Output
    tokens are excluded — they are produced by the model, not part of the
    cache decision.

    Returns 0.0 when the denominator is zero (no input/cache activity).
    """
    denom = (
        entry.input_tokens
        + entry.cache_creation_tokens
        + entry.cache_read_tokens
    )
    if denom == 0:
        return 0.0
    return entry.cache_read_tokens / denom


def dollars_saved(entry: _HasCacheReadOnly, input_price_per_mtok: float) -> float:
    """Dollars cache-reads saved versus paying the uncached input rate.

    `input_price_per_mtok` is the model's plain-input price per *million*
    tokens (Anthropic's standard pricing unit). If those `cache_read_tokens`
    had been re-sent as fresh input, you'd have paid this. Their actual
    cache-read cost is already inside `total_cost`; this estimates the
    *delta*, not the absolute cost.

    Returns 0.0 for a non-positive price (treat as "saving is undefined")
    rather than producing a negative or misleading number.
    """
    if input_price_per_mtok <= 0:
        return 0.0
    return entry.cache_read_tokens / 1_000_000 * input_price_per_mtok


def top_n_by_cost(entries: Iterable, n: int) -> list:
    """Return the `n` highest-cost entries, descending.

    Cost is read from whichever of `total_cost`, `cost`, `cost_usd` is
    present on the entry (handles DailyEntry / SessionEntry, ModelBreakdown,
    BlockEntry uniformly). Missing-all-three falls back to 0.0.

    `n <= 0` returns an empty list. Stable: ties keep input order.
    """
    if n <= 0:
        return []
    items = list(entries)
    items.sort(key=_cost_of, reverse=True)
    return items[:n]


def _cost_of(entry: object) -> float:
    for attr in ("total_cost", "cost", "cost_usd"):
        value = getattr(entry, attr, None)
        if value is not None:
            return float(value)
    return 0.0


def mtd_cost(daily_report: DailyReport, today: date) -> float:
    """Sum of `total_cost` for entries in the same calendar month as `today`.

    Month boundary is taken from `today`'s year+month, so "MTD" follows the
    sidebar's selected today rather than the system clock — keeps the
    function pure.
    """
    prefix = today.strftime("%Y-%m")
    return sum(e.total_cost for e in daily_report.daily if e.date.startswith(prefix))


def today_cost(daily_report: DailyReport, today: date) -> float:
    """Total cost recorded for `today`, or 0.0 if the report has no entry."""
    today_iso = today.isoformat()
    for entry in daily_report.daily:
        if entry.date == today_iso:
            return entry.total_cost
    return 0.0


def aggregate_cache_hit_ratio(daily_report: DailyReport) -> float:
    """Cache hit ratio summed across every entry in the report.

    Computed on totals (not as a mean of per-entry ratios) so days with
    different volumes are weighted correctly.
    """
    total_input = sum(e.input_tokens for e in daily_report.daily)
    total_create = sum(e.cache_creation_tokens for e in daily_report.daily)
    total_read = sum(e.cache_read_tokens for e in daily_report.daily)
    denom = total_input + total_create + total_read
    if denom == 0:
        return 0.0
    return total_read / denom


def active_block_burn(blocks_report: BlocksReport) -> float | None:
    """Cost-per-hour of the currently-active block, or None if there isn't one."""
    for block in blocks_report.blocks:
        if block.is_active and block.burn_rate is not None:
            return block.burn_rate.cost_per_hour
    return None


def daily_cost_by_model(daily_report: DailyReport) -> list[dict]:
    """Long-form rows for the stacked-area chart.

    One row per (day × model) with columns: date, model, family, cost.
    The UI layer can group/colour by either `model` or `family` depending
    on legend density.
    """
    return [
        {
            "date": entry.date,
            "model": breakdown.model_name,
            "family": model_family(breakdown.model_name),
            "cost": breakdown.cost,
        }
        for entry in daily_report.daily
        for breakdown in entry.model_breakdowns
    ]


def daily_token_mix(daily_report: DailyReport) -> list[dict]:
    """Long-form rows for the daily token-mix stacked bar.

    One row per (day × token-kind). `kind` is one of: input, output,
    cache_create, cache_read — the four buckets ccusage reports.
    """
    rows: list[dict] = []
    for entry in daily_report.daily:
        rows.append({"date": entry.date, "kind": "input", "tokens": entry.input_tokens})
        rows.append({"date": entry.date, "kind": "output", "tokens": entry.output_tokens})
        rows.append({"date": entry.date, "kind": "cache_create", "tokens": entry.cache_creation_tokens})
        rows.append({"date": entry.date, "kind": "cache_read", "tokens": entry.cache_read_tokens})
    return rows


def model_family(model_name: str) -> str:
    """Strip date/version suffixes from a model identifier, keep the family.

    Examples:
        claude-opus-4-7            -> opus
        claude-haiku-4-5-20251001  -> haiku
        claude-sonnet-4-6          -> sonnet
        claude-3-5-sonnet-20240620 -> sonnet   (legacy ordering)
        gpt-4o                     -> gpt-4o   (no claude prefix, pass through)
        ""                         -> ""

    The family is the first non-digit-prefixed segment after `claude-`.
    Anything that does not start with `claude-` is returned unchanged so the
    UI can still group/display it.
    """
    if not model_name:
        return model_name
    parts = model_name.split("-")
    if parts[0] != "claude":
        return model_name
    for part in parts[1:]:
        if part and not part[0].isdigit():
            return part
    return model_name
