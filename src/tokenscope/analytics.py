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

import re
from datetime import date, timedelta
from statistics import median
from typing import Iterable, Protocol

from tokenscope.models import (
    BlockEntry,
    BlocksReport,
    DailyEntry,
    DailyReport,
    SessionEntry,
    SessionReport,
)
from tokenscope.query import Query


class _HasCacheTokens(Protocol):
    input_tokens: int
    cache_creation_tokens: int
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


def _cache_hit_ratio_from_counts(
    input_tokens: int, cache_create: int, cache_read: int
) -> float:
    denom = input_tokens + cache_create + cache_read
    if denom == 0:
        return 0.0
    return cache_read / denom


def cache_hit_ratio(entry: _HasCacheTokens) -> float:
    """Fraction of "cache-eligible" tokens that were served by a cache read.

    Formula: `cache_read / (input + cache_create + cache_read)`. Output
    tokens are excluded — they are produced by the model, not part of the
    cache decision.

    Returns 0.0 when the denominator is zero (no input/cache activity).
    """
    return _cache_hit_ratio_from_counts(
        entry.input_tokens,
        entry.cache_creation_tokens,
        entry.cache_read_tokens,
    )


def block_cache_hit_ratio(block: BlockEntry) -> float:
    """Cache hit ratio for an active billing block.

    `BlockTokenCounts` uses the JSON field names ccusage emits for
    blocks (`cacheCreationInputTokens` / `cacheReadInputTokens`),
    which differ from `DailyEntry`'s (`cacheCreationTokens` /
    `cacheReadTokens`). Same formula — different attribute names —
    so this routes the three block counts through the shared
    `_cache_hit_ratio_from_counts` helper rather than duplicating
    the formula.
    """
    c = block.token_counts
    return _cache_hit_ratio_from_counts(
        c.input_tokens, c.cache_creation_input_tokens, c.cache_read_input_tokens
    )


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


def window_cost(daily_report: DailyReport) -> float:
    """Total `total_cost` across every entry in the report (the selected window)."""
    return sum(e.total_cost for e in daily_report.daily)


def last_day_cost(daily_report: DailyReport) -> tuple[str, float] | None:
    """`(date, total_cost)` for the most recent entry in the window.

    Returns None when the report is empty. Independent of system "today" —
    when the user picks a window that ends in the past, this still surfaces
    a meaningful "most-recent day" KPI.
    """
    if not daily_report.daily:
        return None
    latest = max(daily_report.daily, key=lambda e: e.date)
    return latest.date, latest.total_cost


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


def blocks_on_day(
    blocks_report: BlocksReport, day: str, tz: str | None = None
) -> list[BlockEntry]:
    """Non-gap blocks whose local-zone start-of-day matches ``day``.

    Block timestamps are UTC ISO regardless of ccusage's `--timezone`
    flag (only ccusage's daily / weekly / monthly bucketing honours it).
    For "which blocks started on YYYY-MM-DD?" the comparison must happen
    in the user's local zone, otherwise a PST user sees yesterday's
    blocks under tomorrow's date.

    When ``tz`` is None, fall back to the legacy UTC-prefix match so
    pre-slice-14 callers still work.

    Gap blocks are always excluded — they don't carry real usage.
    """
    # Local import — `tz.py` reads /etc/localtime, which we don't want at
    # analytics module load time. utc_iso_to_local_date is pure.
    from tokenscope.tz import utc_iso_to_local_date

    out: list[BlockEntry] = []
    for b in blocks_report.blocks:
        if b.is_gap:
            continue
        if tz is None:
            local_day = b.start_time[:10]
        else:
            local_day = utc_iso_to_local_date(b.start_time, tz) or b.start_time[:10]
        if local_day == day:
            out.append(b)
    return out


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


def model_breakdown(daily_report: DailyReport) -> list[dict]:
    """Window-aggregate per-model rows for the Models breakdown table.

    Each row keeps the full model name (no family collapse), so a user
    running both `claude-opus-4-6` and `claude-opus-4-7` sees each
    version separately.

    Columns:

      * ``model`` — full model id
      * ``family`` — `model_family(model)` result
      * ``tokens`` — total tokens (sum across kinds)
      * ``input``, ``output``, ``cache_create``, ``cache_read`` —
        per-kind token counts (drive the per-model token-kind chart)
      * ``cost`` — total cost USD
      * ``per_mtok`` — blended `$ / 1M tokens` (cost ÷ tokens × 1M)
      * ``share`` — cost fraction of the window total (0–1)
      * ``cache_hit_ratio`` — `cache_read / (input + cache_create +
        cache_read)` for this model
      * ``last_used`` — the latest `YYYY-MM-DD` the model appeared
        in the window, or `None` if never seen (defensive — every row
        is built from at least one daily entry, so this is always set
        in practice).

    Sorted descending by cost so the table reads "where the money
    goes" top-down. Empty input yields an empty list.
    """
    totals: dict[str, dict] = {}
    window_total_cost = 0.0
    for entry in daily_report.daily:
        for b in entry.model_breakdowns:
            row = totals.setdefault(
                b.model_name,
                {
                    "model": b.model_name,
                    "family": model_family(b.model_name),
                    "input": 0,
                    "output": 0,
                    "cache_create": 0,
                    "cache_read": 0,
                    "tokens": 0,
                    "cost": 0.0,
                    "last_used": None,
                },
            )
            row["input"] += b.input_tokens
            row["output"] += b.output_tokens
            row["cache_create"] += b.cache_creation_tokens
            row["cache_read"] += b.cache_read_tokens
            row["tokens"] += (
                b.input_tokens
                + b.output_tokens
                + b.cache_creation_tokens
                + b.cache_read_tokens
            )
            row["cost"] += b.cost
            window_total_cost += b.cost
            if row["last_used"] is None or entry.date > row["last_used"]:
                row["last_used"] = entry.date

    rows = list(totals.values())
    for row in rows:
        row["per_mtok"] = (
            row["cost"] / row["tokens"] * 1_000_000 if row["tokens"] else 0.0
        )
        row["share"] = (
            row["cost"] / window_total_cost if window_total_cost else 0.0
        )
        row["cache_hit_ratio"] = _cache_hit_ratio_from_counts(
            row["input"], row["cache_create"], row["cache_read"]
        )
    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows


def cost_concentration_summary(rows: list[dict]) -> dict | None:
    """For the Models view's "Cost concentration" KPI card.

    Identifies the top-cost model in the window and reports what
    share of total spend it represents. Useful for the "is one
    model dominating?" question without needing the user to read
    the share column.

    Takes the already-built `model_breakdown` rows so the helper
    stays decoupled from `DailyReport` shape — same input shape
    the Models view already has on hand.

    Returns ``{model, family, share}`` for the top row, or
    ``None`` when the row list is empty.
    """
    if not rows:
        return None
    top = max(rows, key=lambda r: r["cost"])
    return {
        "model": top["model"],
        "family": top["family"],
        "share": top["share"],
    }


def friendly_project_label(slug: str, home_slug: str | None = None) -> str:
    """Make a ccusage project slug scannable.

    ccusage encodes a project's absolute path as a slugified string with
    `-` separators (e.g. `-Users-q-johnsmith-Documents-RiderProjects-WorldForge`).
    The encoding is *lossy* — hyphens inside directory names collide
    with the separator. `mini-ollama-ui` looks identical to `mini/ollama/ui`
    in slug form, so any "split on `-` and label the last bit" heuristic
    invents wrong leaves the moment a project name contains a hyphen.

    We deliberately do **not** try to recover directory structure. Instead:

    1. If the slug matches the user's home-directory slug (passed in as
       `home_slug` — sidebar.py computes it from `pathlib.Path.home()`),
       substitute the home prefix with `~`. That's where 90% of the
       sidebar noise lives.
    2. Otherwise, strip the leading `-` and leave the rest verbatim.
       The user reads "Volumes-SSK-Drive--Foo" and instantly recognises
       it as their external drive without us mangling it further.

    Examples (with `home_slug="-Users-q-johnsmith"`):
        "-Users-q-johnsmith"                              → "~"
        "-Users-q-johnsmith-Documents-RiderProjects-tok"  → "~/Documents-RiderProjects-tok"
        "-Users-q-johnsmith-baremetal-audit"              → "~/baremetal-audit"
        "-Volumes-SSK-Drive--ManageLiterature"            → "Volumes-SSK-Drive--ManageLiterature"
        "Unknown Project"                                  → "Unknown Project"   (pass-through)
        ""                                                 → ""                  (pass-through)
    """
    if not slug:
        return slug
    if home_slug:
        if slug == home_slug:
            return "~"
        prefix = home_slug + "-"
        if slug.startswith(prefix):
            return "~/" + slug[len(prefix):]
    if slug.startswith("-"):
        return slug[1:]
    return slug


def short_model_label(model_name: str) -> str:
    """Strip the trailing date suffix from a Claude model identifier.

    `claude-haiku-4-5-20251001` → `claude-haiku-4-5`. The 8-digit date is
    noise in the sidebar; we keep family + version so the user can still
    distinguish opus-4-6 from opus-4-7. Non-Claude names pass through.
    """
    if not model_name or not model_name.startswith("claude-"):
        return model_name
    parts = model_name.split("-")
    # Trailing 8-digit YYYYMMDD: drop it.
    if parts[-1].isdigit() and len(parts[-1]) == 8:
        return "-".join(parts[:-1])
    return model_name


UNKNOWN_MODEL_FAMILY = "other"

# Currently-known Anthropic model families, listed in capability order
# (most-capable first). The dashboard reasons about families, not
# individual model versions — `claude-opus-4-7`, `claude-opus-4-6`,
# `claude-3-5-sonnet-20240620` all collapse to one of these names via
# `model_family`. The list is the authoritative "known" registry the
# chart layer consults when assigning brand-stable colors so each
# family keeps the same hue across every render regardless of which
# subset happens to be in the user's window.
KNOWN_MODEL_FAMILIES: tuple[str, ...] = ("opus", "sonnet", "haiku")


def model_family(model_name: str) -> str:
    """Strip date/version suffixes from a model identifier, keep the family.

    Examples:
        claude-opus-4-7            -> opus
        claude-haiku-4-5-20251001  -> haiku
        claude-sonnet-4-6          -> sonnet
        claude-3-5-sonnet-20240620 -> sonnet   (legacy ordering)
        gpt-4o                     -> gpt-4o   (no claude prefix, pass through)
        ""                         -> "other"  (defensive fallback)
        None                       -> "other"

    The family is the first non-digit-prefixed segment after `claude-`.
    Anything that does not start with `claude-` is returned unchanged so the
    UI can still group/display it.

    Defensive: an empty / None / falsy model_name returns `UNKNOWN_MODEL_FAMILY`
    rather than the empty string. Plotly's JS layer stringifies empty
    category names to the literal `"undefined"` in legends and axis
    labels, which surfaced as a phantom legend entry on the Overview
    Daily-cost chart. Returning a known sentinel here makes the
    fallback explicit and chartable.
    """
    if not model_name:
        return UNKNOWN_MODEL_FAMILY
    parts = model_name.split("-")
    if parts[0] != "claude":
        return model_name
    for part in parts[1:]:
        if part and not part[0].isdigit():
            return part
    return model_name or UNKNOWN_MODEL_FAMILY


def prior_window_query(query: Query) -> Query | None:
    """Build the prior-equivalent-length window for a comparison fetch.

    If the current window is `2026-04-17 → 2026-05-16` (30 days), the
    prior window is `2026-03-18 → 2026-04-16` (also 30 days, ending the
    day before the current window starts).

    Returns None when the query has no explicit `since`/`until`, or
    when either bound is malformed — without parseable bounds we don't
    have a "prior" to compare against. Date strings are emitted in
    ccusage's `YYYYMMDD` format so the returned Query is a drop-in for
    `data.daily(...)`.

    Project / offline flags are carried over unchanged so the comparison
    fetches the same slice of data, just shifted in time.
    """
    since = query.since_date()
    until = query.until_date()
    if since is None or until is None:
        return None
    length = (until - since).days
    if length < 0:
        return None
    prior_until = since - timedelta(days=1)
    prior_since = prior_until - timedelta(days=length)
    return Query(
        since=prior_since.strftime("%Y%m%d"),
        until=prior_until.strftime("%Y%m%d"),
        project=query.project,
        offline=query.offline,
    )


def window_effective_per_mtok(daily_report: DailyReport) -> float | None:
    """Blended `$ / 1M tokens` for the window — what each million tokens
    actually cost end-to-end, weighting input / output / cache_create /
    cache_read together.

    Useful as a "did caching help?" indicator: if your effective rate is
    much lower than the published input rate, caching is doing its job.

    Returns None when the window has no tokens (can't divide).
    """
    total_cost = sum(e.total_cost for e in daily_report.daily)
    total_tokens = sum(
        e.input_tokens + e.output_tokens + e.cache_creation_tokens + e.cache_read_tokens
        for e in daily_report.daily
    )
    if total_tokens == 0:
        return None
    return total_cost / total_tokens * 1_000_000


def typical_burn_rate(blocks_report: BlocksReport) -> float | None:
    """Median `costPerHour` of completed (non-gap, non-active) blocks.

    Used as a baseline on the Live view's burn gauge — "is the current
    burn typical for me or am I on a hot streak?". Returns None when
    fewer than 3 completed blocks with a burn rate exist (median of a
    tiny sample is misleading).
    """
    rates = [
        b.burn_rate.cost_per_hour
        for b in blocks_report.blocks
        if not b.is_gap and not b.is_active and b.burn_rate is not None
    ]
    if len(rates) < 3:
        return None
    return float(median(rates))


def blocks_for_session(
    blocks_report: BlocksReport,
    session: SessionEntry,
    tz: str | None = None,
) -> list[BlockEntry]:
    """Non-gap blocks whose local-zone start-of-day matches the session's
    ``lastActivity`` date.

    Closes the last unbuilt PLAN.md §3.1 drill ("Blocks within session").
    ccusage doesn't expose a join key between sessions and 5-hour billing
    blocks — sessions are conversations, blocks are billing windows, and
    they can overlap many-to-many. The honest heuristic the available
    data supports is "blocks that started on the same local-zone day the
    session was last active". Most sessions are short enough that this
    captures the right set; the session-detail view caption notes the
    approximation so the user isn't surprised.

    Gap blocks are always excluded — they don't carry usage. When ``tz``
    is None, falls back to UTC-prefix matching (legacy behaviour).
    """
    return blocks_on_day(blocks_report, session.last_activity, tz=tz)


def cost_by_kind(daily_report: DailyReport) -> list[dict] | None:
    """Estimate per-kind cost for the window using LiteLLM's pricing.

    For each model in the window:
      cost_per_kind = tokens_of_kind × LiteLLM_rate_for_that_kind / 1M

    Returns one row per kind (input / output / cache_create / cache_read):
      {kind, tokens, est_cost, share}

    `share` is the fraction of the *estimated* total — sums to 1.0.

    Returns ``None`` when LiteLLM pricing isn't reachable AND no cached
    copy exists (offline + first run). The caller should hide the
    Cost-composition panel rather than showing made-up numbers.

    Note: ccusage doesn't break out per-kind cost in its JSON; we apply
    LiteLLM's published rate schedule (same source ccusage uses) to the
    user's token counts. The sum across kinds typically lands within a
    few percent of ccusage's reported total — the gap is model-version
    pricing nuance + promotional discounts.
    """
    from tokenscope.pricing import KINDS, rates_for_model

    tokens_by_kind: dict[str, int] = {k: 0 for k in KINDS}
    cost_by_kind: dict[str, float] = {k: 0.0 for k in KINDS}
    any_rates_available = False
    for entry in daily_report.daily:
        for b in entry.model_breakdowns:
            rates = rates_for_model(b.model_name)
            if rates is None:
                continue
            any_rates_available = True
            counts = {
                "input": b.input_tokens,
                "output": b.output_tokens,
                "cache_create": b.cache_creation_tokens,
                "cache_read": b.cache_read_tokens,
            }
            for kind, n in counts.items():
                tokens_by_kind[kind] += n
                cost_by_kind[kind] += n / 1_000_000 * rates[kind]

    # If we never resolved a single rate (e.g. offline + no cache),
    # signal to the caller to hide the panel rather than render zeroes
    # that would look like "no usage" when in fact rates are unknown.
    if not any_rates_available and any(daily_report.daily):
        return None

    total_est = sum(cost_by_kind.values())
    rows: list[dict] = []
    for kind in KINDS:
        rows.append(
            {
                "kind": kind,
                "tokens": tokens_by_kind[kind],
                "est_cost": cost_by_kind[kind],
                "share": (cost_by_kind[kind] / total_est) if total_est else 0.0,
            }
        )
    return rows


def block_token_counts_by_kind(block: BlockEntry) -> dict[str, int]:
    """Block's cumulative token counts as a `{kind: count}` dict using
    the canonical `pricing.KINDS` keys (`input` / `output` /
    `cache_create` / `cache_read`).

    Single mapping point between `BlockTokenCounts`'s JSON field names
    (`cacheCreationInputTokens` / `cacheReadInputTokens`) — which
    differ from `DailyEntry`'s field names — and the kind keys every
    downstream consumer uses. Every chart builder and view renderer
    that needs per-kind block counts routes through here; adding a
    new caller does NOT re-establish the mapping ad-hoc.

    Insertion order matches `KINDS` order so callers iterating
    `block_token_counts_by_kind(block)` see the canonical
    input → output → cache_create → cache_read sequence (relied on
    by the Live view's KPI card order, the composition bar's
    segment order, and the mini-table's row order).
    """
    c = block.token_counts
    return {
        "input": c.input_tokens,
        "output": c.output_tokens,
        "cache_create": c.cache_creation_input_tokens,
        "cache_read": c.cache_read_input_tokens,
    }


def block_cost_by_kind(block: BlockEntry) -> list[dict] | None:
    """Estimate per-kind cost contribution for an active block.

    The block aggregates tokens across every model used in the
    window, but ccusage doesn't break out per-kind cost in the
    block JSON. We use the first resolvable model in `block.models`
    as the rate source to derive a per-kind RATIO, then rescale so
    the per-kind costs sum exactly to `block.cost_usd`. The total
    matches what ccusage reported; only the split between kinds is
    an approximation.

    Returns one row per kind with the same shape as
    `cost_by_kind`: `{kind, tokens, est_cost, share}`. `share` is
    the rate-weighted fraction (sums to 1.0 when any kind has
    tokens), independent of the absolute total.

    Returns `None` when no model in the block has resolvable rates
    (offline + no cached LiteLLM pricing). The caller hides the
    cost line on the KPI cards rather than rendering zeroes that
    would look like "no cost" when in fact rates are unknown.
    """
    from tokenscope.pricing import KINDS, rates_for_model

    if not block.models:
        return None

    rates = None
    for model_name in block.models:
        candidate = rates_for_model(model_name)
        if candidate is not None:
            rates = candidate
            break
    if rates is None:
        return None

    counts = block_token_counts_by_kind(block)
    notional: dict[str, float] = {
        k: counts[k] * rates[k] / 1_000_000 for k in KINDS
    }
    notional_total = sum(notional.values())
    actual_total = block.cost_usd

    rows: list[dict] = []
    for kind in KINDS:
        if notional_total > 0:
            share = notional[kind] / notional_total
            est_cost = share * actual_total
        else:
            share = 0.0
            est_cost = 0.0
        rows.append(
            {
                "kind": kind,
                "tokens": counts[kind],
                "est_cost": est_cost,
                "share": share,
            }
        )
    return rows


def cache_savings(daily_report: DailyReport) -> dict | None:
    """Estimated dollar savings from caching `cache_read` tokens
    vs. paying the full input rate for the same tokens.

    For each model breakdown in the window:
      saving = (rate.input - rate.cache_read) × cache_read_tokens / 1M

    Summed across every model breakdown across every day. The
    formula is the rate DELTA — not the full input rate — because
    every cache_read token still costs something (the discounted
    cache_read rate); the saving is the discount, not the full
    rate. This was the framing problem that broke the previous
    `$X saved` headline two iterations back.

    Returns a dict ``{savings_usd, actual_cost_usd, uncached_cost_usd}``
    where ``uncached_cost_usd = actual_cost_usd + savings_usd``
    (the hypothetical "what would I have paid without caching"
    figure the Cache view shows under the savings hero).

    Returns ``None`` when ZERO model breakdowns resolved a rate
    (offline + no LiteLLM cache). The caller hides the hero
    rather than showing made-up zeros.
    """
    from tokenscope.pricing import rates_for_model

    savings_total = 0.0
    actual_total = 0.0
    any_rates_available = False
    for entry in daily_report.daily:
        actual_total += entry.total_cost
        for b in entry.model_breakdowns:
            rates = rates_for_model(b.model_name)
            if rates is None:
                continue
            any_rates_available = True
            savings_total += (
                (rates["input"] - rates["cache_read"])
                * b.cache_read_tokens
                / 1_000_000
            )
    if not any_rates_available:
        return None
    return {
        "savings_usd": savings_total,
        "actual_cost_usd": actual_total,
        "uncached_cost_usd": actual_total + savings_total,
    }


def daily_cache_savings(daily_report: DailyReport) -> list[dict] | None:
    """Per-day version of `cache_savings` — one row per date with
    ``{date, savings_usd}``. Returns rows in ascending-date order.

    Same formula as `cache_savings` applied per-entry. Returns
    ``None`` when no rates resolve for any model (offline + no
    cache) so the chart layer hides the panel.
    """
    from tokenscope.pricing import rates_for_model

    any_rates_available = False
    rows: list[dict] = []
    for entry in sorted(daily_report.daily, key=lambda e: e.date):
        day_savings = 0.0
        for b in entry.model_breakdowns:
            rates = rates_for_model(b.model_name)
            if rates is None:
                continue
            any_rates_available = True
            day_savings += (
                (rates["input"] - rates["cache_read"])
                * b.cache_read_tokens
                / 1_000_000
            )
        rows.append({"date": entry.date, "savings_usd": day_savings})
    if not any_rates_available:
        return None
    return rows


def per_model_cache_performance(daily_report: DailyReport) -> list[dict] | None:
    """One row per unique model in the window with cache stats.

    Each row carries:
      * ``model`` — full model name
      * ``cache_hit_ratio`` — `cache_read / (input + cache_create + cache_read)`
      * ``cache_read_tokens`` — total reads served from cache
      * ``cache_create_tokens`` — total writes / fresh cache loads
      * ``savings_usd`` — `(input_rate − cache_read_rate) ×
        cache_read_tokens / 1M` using THIS model's rates
      * ``has_rates`` — False when LiteLLM didn't resolve a rate
        for the model (the savings figure is then 0.0 but the
        cache hit ratio + token counts are still accurate)

    Sorted by `cache_read_tokens` descending so the heaviest cache
    user reads first.

    Returns ``None`` when the report has no model breakdowns at all
    (empty window) — the UI hides the per-model panel rather than
    rendering an empty table.
    """
    from tokenscope.pricing import rates_for_model

    aggregates: dict[str, dict[str, int | float]] = {}
    for entry in daily_report.daily:
        for b in entry.model_breakdowns:
            agg = aggregates.setdefault(
                b.model_name,
                {
                    "input": 0,
                    "output": 0,
                    "cache_create": 0,
                    "cache_read": 0,
                },
            )
            agg["input"] += b.input_tokens
            agg["output"] += b.output_tokens
            agg["cache_create"] += b.cache_creation_tokens
            agg["cache_read"] += b.cache_read_tokens
    if not aggregates:
        return None

    rows: list[dict] = []
    for model_name, agg in aggregates.items():
        cache_eligible = agg["input"] + agg["cache_create"] + agg["cache_read"]
        ratio = (agg["cache_read"] / cache_eligible) if cache_eligible else 0.0
        rates = rates_for_model(model_name)
        if rates is not None:
            savings = (
                (rates["input"] - rates["cache_read"])
                * agg["cache_read"]
                / 1_000_000
            )
            has_rates = True
        else:
            savings = 0.0
            has_rates = False
        rows.append(
            {
                "model": model_name,
                "cache_hit_ratio": ratio,
                "cache_read_tokens": agg["cache_read"],
                "cache_create_tokens": agg["cache_create"],
                "savings_usd": savings,
                "has_rates": has_rates,
            }
        )
    rows.sort(key=lambda r: r["cache_read_tokens"], reverse=True)
    return rows


def cache_data_range(daily_report: DailyReport) -> tuple[str, str] | None:
    """First and last date with any cache activity (cache_create OR
    cache_read tokens > 0) in the window.

    Drives the "Cache data available from … onward" banner on the
    Cache view: when the user's sidebar window starts before any
    cache data was emitted (e.g. caching only kicked in part-way
    through the 30-day window), the banner surfaces that gap
    explicitly so the user isn't left wondering why the chart's
    X-axis is narrower than the sidebar's date range.

    Returns ``None`` when no entry in the window has cache
    activity.
    """
    cache_dates = sorted(
        e.date
        for e in daily_report.daily
        if e.cache_creation_tokens > 0 or e.cache_read_tokens > 0
    )
    if not cache_dates:
        return None
    return cache_dates[0], cache_dates[-1]


# --- formatting helpers (presentation-layer, but pure / unit-testable) ---


_COMPACT_THOUSAND = 1_000
_COMPACT_MILLION = 1_000_000
_COMPACT_BILLION = 1_000_000_000


_BOLD_NUMBER_PATTERN = re.compile(
    # Order matters: dollar amounts first (so "$1,020.73" is caught
    # whole, not as "$1," + "020.73"); then signed-or-unsigned
    # percentages.
    r"(\$[\d,]+\.\d{2}|[+\-]?\d+(?:\.\d+)?%)"
)


def bold_numbers_in_insight(text: str) -> str:
    """Wrap dollar amounts (``$X.XX``) and percentages (``+91%``,
    ``99.0%``) in HTML ``<strong>`` tags. Used by the Overview
    insight renderer to draw the eye to the key figures in the
    narrative paragraph.

    Pure text → text/HTML transform — kept here (not in the
    Streamlit-coupled renderer) so the regex contract is
    unit-testable without a Streamlit context.
    """
    return _BOLD_NUMBER_PATTERN.sub(r"<strong>\1</strong>", text)


def collapse_composition_rows(
    rows: list[dict], *, hide_threshold: float
) -> list[dict]:
    """Group cost-composition rows whose ``share`` is below
    ``hide_threshold`` into a single ``"other"`` row.

    The cost-composition table on Overview lists per-kind contribution
    to window spend. With four kinds (input / output / cache_create /
    cache_read), the share is often dominated by one (cache_read ≈ 99%)
    and the smallest is sub-0.01% — a row that adds no signal and a
    visible-share bar that clips to zero pixels.

    ``hide_threshold`` is a *share fraction* (0–1), not a percentage.
    Rows at or above the threshold pass through unchanged; rows below
    are summed into a single "other" row whose tokens, est_cost, and
    share are the aggregate of the collapsed rows. If no rows
    qualify for collapse (or only one row is below threshold), the
    input is returned unchanged.
    """
    if hide_threshold <= 0:
        return list(rows)
    below = [r for r in rows if r["share"] < hide_threshold]
    above = [r for r in rows if r["share"] >= hide_threshold]
    if len(below) < 2:
        # Either nothing to collapse or exactly one small row, which
        # we leave alone — collapsing one row into an "other" row
        # would be a relabel, not a simplification.
        return list(rows)
    other = {
        "kind": "other",
        "tokens": sum(r["tokens"] for r in below),
        "est_cost": sum(r["est_cost"] for r in below),
        "share": sum(r["share"] for r in below),
    }
    return above + [other]


def format_timezone_for_display(tz: str) -> str:
    """Convert an IANA timezone identifier to display copy.

    IANA identifiers use underscores in place of spaces (``America/New_York``)
    so they're filename-safe and parser-friendly. The dashboard's UI
    copy should show spaces — the underscore is implementation
    detail leaking through. Pass-through for already-spaced inputs.
    """
    if not tz:
        return ""
    return tz.replace("_", " ")


def format_compact_int(n: int) -> str:
    """Compact integer formatting for token counts.

    Below 1M: thousand-separated (``7,358``).
    1M–1B:    one-decimal M (``4.9M``, ``15.7M``).
    >=1B:     two-decimal B (``1.60B``).

    No K abbreviation — at the magnitudes Claude usage hits (tens of
    thousands at the low end), comma-grouping is cleaner than `7.4K`
    on a dashboard, and the user explicitly called out `7,358 /
    4.9M / 15.7M / 1.6B` as the desired reading.

    Negative inputs format as ``-`` + the positive-side formatting,
    which is uncommon in practice (token counts are non-negative)
    but keeps the function total over the int domain.
    """
    if n < 0:
        return "-" + format_compact_int(-n)
    if n < _COMPACT_MILLION:
        return f"{n:,}"
    if n < _COMPACT_BILLION:
        return f"{n / _COMPACT_MILLION:.1f}M"
    return f"{n / _COMPACT_BILLION:.2f}B"


# --- Overview-page summary primitives ------------------------------------


def spike_day(
    daily_report: DailyReport, threshold_multiplier: float
) -> tuple[str, float] | None:
    """Return ``(date, cost)`` for the day with cost above
    ``threshold_multiplier * median``, or ``None`` if no day qualifies.

    Used by the Overview cost chart to annotate a single notable
    outlier in plain language ("Apr 18 · $447") rather than leaving
    the user to wonder why one bar dwarfs the rest. The threshold is
    operator-tunable via ``[overview] spike_threshold_median_multiplier``;
    the conventional outlier heuristic is ``3.0``.

    Returns the highest-cost qualifying day so a window with several
    spikes still surfaces one annotation rather than crowding the
    chart.
    """
    entries = daily_report.daily
    if len(entries) < 3:
        return None
    costs = [e.total_cost for e in entries]
    med = median(costs)
    if med <= 0:
        return None
    threshold = threshold_multiplier * med
    qualifying = [(e.date, e.total_cost) for e in entries if e.total_cost > threshold]
    if not qualifying:
        return None
    return max(qualifying, key=lambda r: r[1])


def overview_insight(
    *,
    window_total_cost: float,
    window_days: int,
    prior_total: float | None,
    spike: tuple[str, float] | None,
    cache_hit_ratio: float,
) -> str:
    """Build the Overview insight-summary paragraph from rollup numbers.

    Returns plain prose (no Markdown). Sentences are stitched together
    conditionally — missing inputs (no prior period to compare, no
    spike to call out) just omit their sentence rather than producing
    null-laden output.

    The first sentence always exists ("You spent $X over the last N
    days."); the prior-period comparison and spike sentences are
    conditional; the cache-hit sentence appears when the ratio is
    non-zero.
    """
    sentences: list[str] = []

    headline = f"You spent ${window_total_cost:,.2f} over the last {window_days} days"
    if prior_total and prior_total > 0:
        change = (window_total_cost - prior_total) / prior_total
        direction = "up" if change >= 0 else "down"
        headline += (
            f", {direction} {abs(change):.0%} vs the prior {window_days} days."
        )
    else:
        headline += "."
    sentences.append(headline)

    if spike is not None and window_total_cost > 0:
        spike_date, spike_cost = spike
        share = spike_cost / window_total_cost
        sentences.append(
            f"{spike_date} alone accounted for ${spike_cost:,.2f} "
            f"({share:.0%} of the window)."
        )

    if cache_hit_ratio > 0:
        sentences.append(
            f"Cache reads served {cache_hit_ratio:.1%} of input-side tokens."
        )

    return " ".join(sentences)

