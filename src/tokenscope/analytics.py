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

from datetime import date, datetime, timedelta
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
    version separately — the per-family rollup happens in the Sankey
    and the donut, not here.

    Columns: ``model``, ``family``, ``tokens``, ``cost``, ``per_mtok``,
    ``share`` (cost fraction of the window). Returned descending by cost
    so the table reads "where the money goes" top-down. Empty input
    yields an empty list.
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
                    "tokens": 0,
                    "cost": 0.0,
                },
            )
            row["tokens"] += (
                b.input_tokens
                + b.output_tokens
                + b.cache_creation_tokens
                + b.cache_read_tokens
            )
            row["cost"] += b.cost
            window_total_cost += b.cost

    rows = list(totals.values())
    for row in rows:
        row["per_mtok"] = (
            row["cost"] / row["tokens"] * 1_000_000 if row["tokens"] else 0.0
        )
        row["share"] = (
            row["cost"] / window_total_cost if window_total_cost else 0.0
        )
    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows


def token_flow_sankey_data(
    daily_report: DailyReport,
    *,
    value_mode: str = "tokens",
    top_n: int | None = None,
) -> dict:
    """Build Sankey-compatible nodes + links for the models view.

    Flow: token-kind (input/output/cache_create/cache_read) → model family.
    Family nodes carry the family's aggregate cost in their label so the
    "→ cost" direction PLAN.md §3.2 calls for is conveyed without an
    additional mis-scaled layer.

    Parameters:
        value_mode: ``"tokens"`` (default) makes link widths proportional
            to token counts. ``"cost"`` proportionally attributes each
            family's total cost across its kinds, so total link width
            equals total window cost. The cost-mode value is necessarily
            an approximation — ccusage doesn't break out per-kind
            pricing, so we apportion by token share. The header label
            on each family stays in dollars either way.
        top_n: when given, keep the N highest-cost families and collapse
            the rest into a single "Others" node. Useful when 8+
            families turn the Sankey into spaghetti.

    Each link's ``customdata`` carries two extra fields for the Plotly
    hovertemplate: the absolute token count and the family's aggregate
    cost — so the user sees both axes regardless of which mode they're in.

    Returns a dict shaped for ``go.Sankey``:
        {labels, sources, targets, values, customdata, value_mode}

    Empty report → all-empty lists (caller short-circuits).
    """
    if value_mode not in ("tokens", "cost"):
        raise ValueError(f"value_mode must be 'tokens' or 'cost', got {value_mode!r}")

    KINDS = ("input", "output", "cache_create", "cache_read")
    tokens_by_kind_family: dict[tuple[str, str], int] = {}
    cost_by_family: dict[str, float] = {}
    tokens_by_family: dict[str, int] = {}

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
            family_total = 0
            for kind, n in counts.items():
                key = (kind, family)
                tokens_by_kind_family[key] = tokens_by_kind_family.get(key, 0) + n
                family_total += n
            tokens_by_family[family] = tokens_by_family.get(family, 0) + family_total

    families = sorted(cost_by_family.keys())
    if not families:
        return {
            "labels": [],
            "sources": [],
            "targets": [],
            "values": [],
            "customdata": [],
            "value_mode": value_mode,
        }

    # Top-N collapse: keep the N highest-cost families, fold the rest
    # into an "Others" bucket. Costs and tokens accumulate into the bucket
    # so node labels and link values stay correct.
    if top_n is not None and 0 < top_n < len(families):
        ranked = sorted(families, key=lambda f: cost_by_family[f], reverse=True)
        kept = set(ranked[:top_n])
        OTHERS = "Others"
        cost_by_family[OTHERS] = sum(
            cost_by_family[f] for f in families if f not in kept
        )
        tokens_by_family[OTHERS] = sum(
            tokens_by_family[f] for f in families if f not in kept
        )
        for (kind, fam), v in list(tokens_by_kind_family.items()):
            if fam not in kept:
                key = (kind, OTHERS)
                tokens_by_kind_family[key] = tokens_by_kind_family.get(key, 0) + v
                del tokens_by_kind_family[(kind, fam)]
        for f in list(families):
            if f not in kept:
                del cost_by_family[f]
                del tokens_by_family[f]
        families = sorted(cost_by_family.keys())

    labels = list(KINDS) + [
        f"{fam} (${cost_by_family[fam]:,.2f})" for fam in families
    ]
    family_idx = {fam: len(KINDS) + i for i, fam in enumerate(families)}

    sources: list[int] = []
    targets: list[int] = []
    values: list[float] = []
    customdata: list[tuple[int, float]] = []
    for kind_idx, kind in enumerate(KINDS):
        for fam in families:
            token_count = tokens_by_kind_family.get((kind, fam), 0)
            if token_count <= 0:
                continue
            if value_mode == "cost":
                # Proportionally attribute the family's cost across kinds.
                fam_tokens = tokens_by_family[fam] or 1
                width = cost_by_family[fam] * token_count / fam_tokens
            else:
                width = float(token_count)
            sources.append(kind_idx)
            targets.append(family_idx[fam])
            values.append(width)
            customdata.append((token_count, cost_by_family[fam]))
    return {
        "labels": labels,
        "sources": sources,
        "targets": targets,
        "values": values,
        "customdata": customdata,
        "value_mode": value_mode,
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


def prior_window_query(query: Query) -> Query | None:
    """Build the prior-equivalent-length window for a comparison fetch.

    If the current window is `2026-04-17 → 2026-05-16` (30 days), the
    prior window is `2026-03-18 → 2026-04-16` (also 30 days, ending the
    day before the current window starts).

    Returns None when the query has no explicit `since`/`until` — without
    bounds we don't have a "prior" to compare against. Date strings are
    parsed/emitted in ccusage's `YYYYMMDD` format so the returned Query
    is a drop-in for `data.daily(...)`.

    Project / offline flags are carried over unchanged so the comparison
    fetches the same slice of data, just shifted in time.
    """
    if not query.since or not query.until:
        return None
    try:
        since = datetime.strptime(query.since, "%Y%m%d").date()
        until = datetime.strptime(query.until, "%Y%m%d").date()
    except ValueError:
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
