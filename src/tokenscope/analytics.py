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

from tokenscope.models import (
    BlockEntry,
    BlocksReport,
    DailyEntry,
    DailyReport,
    SessionEntry,
    SessionReport,
)


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


def find_daily_entry(daily_report: DailyReport, day: str) -> DailyEntry | None:
    """Return the entry for `day` (YYYY-MM-DD), or None if absent."""
    for entry in daily_report.daily:
        if entry.date == day:
            return entry
    return None


def sessions_on_day(session_report: SessionReport, day: str) -> list[SessionEntry]:
    """Sessions whose `lastActivity` equals `day` (YYYY-MM-DD).

    ccusage records `lastActivity` per-session, not per-message, so this
    catches sessions that were most recently touched on the given day —
    it will not catch a session whose activity spanned the day but ended
    later. Good enough for the drill-down's "sessions on this day" panel.
    """
    return [s for s in session_report.sessions if s.last_activity == day]


def blocks_on_day(blocks_report: BlocksReport, day: str) -> list[BlockEntry]:
    """Non-gap blocks whose `startTime` falls on `day` (YYYY-MM-DD, UTC).

    `startTime` is ISO 8601 with a `Z` suffix, so prefix comparison works
    against `YYYY-MM-DD`. Gap blocks are filtered out — they don't carry
    real usage and would clutter the table.
    """
    return [
        b
        for b in blocks_report.blocks
        if not b.is_gap and b.start_time.startswith(day)
    ]


def find_session(session_report: SessionReport, session_id: str) -> SessionEntry | None:
    for s in session_report.sessions:
        if s.session_id == session_id:
            return s
    return None


def find_block(blocks_report: BlocksReport, block_id: str) -> BlockEntry | None:
    for b in blocks_report.blocks:
        if b.id == block_id:
            return b
    return None


def cost_share_by_model(entry: DailyEntry | SessionEntry) -> list[dict]:
    """Donut data for a single entry's model breakdown.

    Returns `[{model, family, cost}]`. Used by day-detail and session-detail
    views. Empty list when the entry has no breakdowns (defensive — every
    entry ccusage emits has at least one).
    """
    return [
        {
            "model": b.model_name,
            "family": model_family(b.model_name),
            "cost": b.cost,
        }
        for b in entry.model_breakdowns
    ]


def filter_daily_by_models(
    daily_report: DailyReport, selected: Iterable[str]
) -> DailyReport:
    """Return a *new* DailyReport with breakdowns restricted to `selected`.

    Per-entry token/cost totals are recomputed from the filtered breakdowns
    so the KPI cards and charts stay internally consistent. Top-level
    `totals` is recomputed across the surviving entries. Entries with no
    surviving breakdowns are dropped.

    `selected` of None or empty means "keep everything" (sensible default
    for "no filter applied"). To pass an empty selection through, the
    caller should special-case it before reaching here.
    """
    keep = set(selected) if selected else None
    if not keep:
        return daily_report
    new_entries: list[DailyEntry] = []
    for entry in daily_report.daily:
        kept = [b for b in entry.model_breakdowns if b.model_name in keep]
        if not kept:
            continue
        new_entries.append(
            DailyEntry(
                date=entry.date,
                inputTokens=sum(b.input_tokens for b in kept),
                outputTokens=sum(b.output_tokens for b in kept),
                cacheCreationTokens=sum(b.cache_creation_tokens for b in kept),
                cacheReadTokens=sum(b.cache_read_tokens for b in kept),
                totalTokens=sum(
                    b.input_tokens
                    + b.output_tokens
                    + b.cache_creation_tokens
                    + b.cache_read_tokens
                    for b in kept
                ),
                totalCost=sum(b.cost for b in kept),
                modelsUsed=[b.model_name for b in kept],
                modelBreakdowns=kept,
            )
        )
    return DailyReport(
        daily=new_entries,
        totals=_totals_from_entries(new_entries),
    )


def available_models(daily_report: DailyReport) -> list[str]:
    """Sorted unique model names that appear anywhere in the report."""
    seen: set[str] = set()
    for entry in daily_report.daily:
        seen.update(entry.models_used)
    return sorted(seen)


def _totals_from_entries(entries: list[DailyEntry]):
    from tokenscope.models import Totals  # local import to avoid module-load cost

    return Totals(
        inputTokens=sum(e.input_tokens for e in entries),
        outputTokens=sum(e.output_tokens for e in entries),
        cacheCreationTokens=sum(e.cache_creation_tokens for e in entries),
        cacheReadTokens=sum(e.cache_read_tokens for e in entries),
        totalTokens=sum(e.total_tokens for e in entries),
        totalCost=sum(e.total_cost for e in entries),
    )


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


def daily_cache_hit_ratio(daily_report: DailyReport) -> list[tuple[str, float]]:
    """Per-day cache hit ratio, in ascending date order.

    Returns `[(date, ratio), ...]` where ratio uses the same
    `cache_read / (input + cache_create + cache_read)` formula as
    `cache_hit_ratio` but applied per-day. Empty input → empty list.
    """
    entries = sorted(daily_report.daily, key=lambda e: e.date)
    return [(e.date, cache_hit_ratio(e)) for e in entries]


def daily_dollars_saved(daily_report: DailyReport) -> list[dict]:
    """Per-day, per-model rows of `dollars_saved` for the cache view.

    Each row is `{date, model, family, dollars_saved}`. Cost is computed
    from `tokenscope.pricing.input_price_per_mtok(model)` — i.e. the
    cache-read tokens valued at uncached input rates. Empty report →
    empty list.
    """
    # Local import to avoid an analytics → pricing → analytics cycle at import time.
    from tokenscope.pricing import input_price_per_mtok

    rows: list[dict] = []
    for entry in daily_report.daily:
        for breakdown in entry.model_breakdowns:
            price = input_price_per_mtok(breakdown.model_name)
            saved = breakdown.cache_read_tokens / 1_000_000 * price
            rows.append(
                {
                    "date": entry.date,
                    "model": breakdown.model_name,
                    "family": model_family(breakdown.model_name),
                    "dollars_saved": saved,
                }
            )
    return rows


def token_flow_sankey_data(daily_report: DailyReport) -> dict:
    """Build Sankey-compatible nodes + links for the models view.

    Flow: token-kind (input/output/cache_create/cache_read) → model family.
    Family nodes are labelled with the family's aggregate cost so the
    "→ cost" direction PLAN.md §3.2 calls for is conveyed in the node
    label rather than as a separate (mis-scaled) layer.

    Returns a dict shaped for `go.Sankey`:
        {labels: [str], sources: [int], targets: [int], values: [int]}

    Empty report → all-empty lists (caller short-circuits).
    """
    KINDS = ("input", "output", "cache_create", "cache_read")
    tokens_by_kind_family: dict[tuple[str, str], int] = {}
    cost_by_family: dict[str, float] = {}

    for entry in daily_report.daily:
        for b in entry.model_breakdowns:
            family = model_family(b.model_name)
            cost_by_family[family] = cost_by_family.get(family, 0.0) + b.cost
            counts = {
                "input": b.input_tokens,
                "output": b.output_tokens,
                "cache_create": b.cache_creation_tokens,
                "cache_read": b.cache_read_tokens,
            }
            for kind, n in counts.items():
                key = (kind, family)
                tokens_by_kind_family[key] = tokens_by_kind_family.get(key, 0) + n

    families = sorted(cost_by_family.keys())
    if not families:
        return {"labels": [], "sources": [], "targets": [], "values": []}

    labels = list(KINDS) + [
        f"{fam} (${cost_by_family[fam]:,.2f})" for fam in families
    ]
    family_idx = {fam: len(KINDS) + i for i, fam in enumerate(families)}

    sources: list[int] = []
    targets: list[int] = []
    values: list[int] = []
    for kind_idx, kind in enumerate(KINDS):
        for fam in families:
            v = tokens_by_kind_family.get((kind, fam), 0)
            if v > 0:
                sources.append(kind_idx)
                targets.append(family_idx[fam])
                values.append(v)
    return {"labels": labels, "sources": sources, "targets": targets, "values": values}


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
