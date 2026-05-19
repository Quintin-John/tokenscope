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
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Iterable, Protocol

from tokenscope.models import (
    BlockEntry,
    BlocksReport,
    DailyByProjectReport,
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


def find_session(
    session_report: SessionReport,
    session_id: str,
    project_path: str | None = None,
) -> SessionEntry | None:
    """Resolve a session row by `(session_id, project_path)`.

    `session_id` alone is NOT unique across projects: ccusage slugs
    each Claude Code project's `subagents/` directory as
    `sessionId="subagents"`, so two projects with subagent runs
    produce two `SessionEntry` instances sharing that id. The fix
    is to disambiguate by the `(session_id, project_path)` tuple,
    which is unique by construction.

    Resolution rules — fail closed when the input is ambiguous,
    never silently return "first match by id alone":

      1. `project_path` is given AND a row matches both fields →
         return that row.
      2. `project_path` is given AND no row matches both (the
         session has aged out of the window, or the URL was
         tampered) → return None.
      3. `project_path` is None AND exactly one row matches the
         `session_id` → return that row. The lookup is unambiguous;
         legacy shareable URLs without `session_project` still
         resolve correctly here.
      4. `project_path` is None AND multiple rows match the
         `session_id` → return None. Caller must disambiguate (e.g.
         re-open the session from the day view, which routes with
         `session_project` set). Returning the "first" match would
         be the original bug.
    """
    matches = [s for s in session_report.sessions if s.session_id == session_id]
    if not matches:
        return None
    if project_path is not None:
        for s in matches:
            if s.project_path == project_path:
                return s
        return None
    if len(matches) == 1:
        return matches[0]
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


def _filter_entries_by_models(
    entries: Iterable[DailyEntry], keep: set[str]
) -> list[DailyEntry]:
    """Internal: return a list of new DailyEntry objects with breakdowns
    restricted to `keep`. Per-entry token/cost totals are recomputed from
    the surviving breakdowns; entries with no surviving breakdowns are
    dropped. The `project` field is preserved verbatim — relevant when
    the source `DailyReport` came from `--project=<id>` (`project` set)
    versus from `daily_by_project` (`project` is None and the project
    identity lives in the outer dict key). Shared core for the two public
    filter entry-points so both shapes apply identical filter semantics.
    """
    new_entries: list[DailyEntry] = []
    for entry in entries:
        kept = [b for b in entry.model_breakdowns if b.model_name in keep]
        if not kept:
            continue
        new_entries.append(
            DailyEntry(
                date=entry.date,
                project=entry.project,
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
    return new_entries


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
    new_entries = _filter_entries_by_models(daily_report.daily, keep)
    return DailyReport(
        daily=new_entries,
        totals=_totals_from_entries(new_entries),
    )


def filter_daily_by_project_models(
    report: DailyByProjectReport, selected: Iterable[str]
) -> DailyByProjectReport:
    """Return a *new* DailyByProjectReport with breakdowns restricted to
    `selected`. Filter semantics match `filter_daily_by_models` exactly
    (shared `_filter_entries_by_models` core): entries with no surviving
    breakdowns are dropped; projects with no surviving entries are dropped;
    top-level totals are recomputed across all surviving entries.

    `selected` of None or empty is passthrough — the original report is
    returned unchanged so the caller can rely on `result is report` to
    detect "no filter applied".
    """
    keep = set(selected) if selected else None
    if not keep:
        return report
    new_projects: dict[str, list[DailyEntry]] = {}
    for project, entries in report.projects.items():
        filtered = _filter_entries_by_models(entries, keep)
        if filtered:
            new_projects[project] = filtered
    all_entries = [e for entries in new_projects.values() for e in entries]
    return DailyByProjectReport(
        projects=new_projects,
        totals=_totals_from_entries(all_entries),
    )


def _available_models_from(entries: Iterable[DailyEntry]) -> list[str]:
    """Shared core for `available_models` / `available_models_by_project`.
    One model-discovery rule, two thin shape adapters above it — the two
    public entry-points cannot drift on what counts as a "seen" model."""
    seen: set[str] = set()
    for entry in entries:
        seen.update(entry.models_used)
    return sorted(seen)


def available_models(daily_report: DailyReport) -> list[str]:
    """Sorted unique model names that appear anywhere in the report."""
    return _available_models_from(daily_report.daily)


def available_models_by_project(report: DailyByProjectReport) -> list[str]:
    """Sorted unique model names across every project's entries in the
    by-project report. Used by the Daily view's `load_daily_by_project`
    to decide whether the sidebar model-multiselect narrows the data;
    sibling of `available_models` so both data paths apply identical
    "what models are in this window" semantics.
    """
    return _available_models_from(
        e for entries in report.projects.values() for e in entries
    )


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


@dataclass(frozen=True, slots=True)
class DailyCell:
    """One cell of the Daily view's per-`(date, model, project)` grid.

    Stores the four primitive token kinds and cost only. `total_tokens`
    is a derived `@property` so there is exactly one rule for summing
    a cell's tokens — re-derived in `DailySummary` and `WindowTotals`
    the same way. Frozen + slots so cells are immutable and cheap.

    `project` is ccusage's project key (the dash-encoded cwd from
    `daily --instances`); the UI layer is responsible for rendering it
    via `friendly_project_label` rather than this dataclass embedding
    a display-format dependency.
    """

    date: str
    model: str
    project: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost: float

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


@dataclass(frozen=True, slots=True)
class DailySummary:
    """Per-day rollup derived from a list of `DailyCell`. Carries the
    same four token kinds + cost as a cell plus the distinct
    model/project counts used by the day-row collapsed header
    (`N models · N projects`).
    """

    date: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost: float
    distinct_models: int
    distinct_projects: int

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


@dataclass(frozen=True, slots=True)
class WindowTotals:
    """Window-wide rollup derived from a list of `DailyCell`. Same six
    numeric fields as ccusage's `Totals` shape but as a frozen
    dataclass — the UI layer's totals card consumes this without
    pinning itself to the pydantic class.
    """

    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost: float

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


def daily_cells(report: DailyByProjectReport) -> list[DailyCell]:
    """Flatten `DailyByProjectReport` into one `DailyCell` per
    `(date, model, project)` combination ccusage actually emitted.

    No sorting: the renderer decides display order. No empty-cell
    synthesis for `(date, model, project)` combinations ccusage didn't
    emit — those genuinely had zero activity and a real row with all
    zeros would be a false positive.
    """
    cells: list[DailyCell] = []
    for project, entries in report.projects.items():
        for entry in entries:
            for b in entry.model_breakdowns:
                cells.append(
                    DailyCell(
                        date=entry.date,
                        model=b.model_name,
                        project=project,
                        input_tokens=b.input_tokens,
                        output_tokens=b.output_tokens,
                        cache_creation_tokens=b.cache_creation_tokens,
                        cache_read_tokens=b.cache_read_tokens,
                        cost=b.cost,
                    )
                )
    return cells


def daily_summaries(cells: Iterable[DailyCell]) -> list[DailySummary]:
    """Per-day rollups from `DailyCell` list. Returned newest-first so the
    Daily tab can render day-rows in descending date order without a
    second sort at the call site.
    """
    by_date: dict[str, dict] = {}
    for cell in cells:
        row = by_date.setdefault(
            cell.date,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "cost": 0.0,
                "models": set(),
                "projects": set(),
            },
        )
        row["input_tokens"] += cell.input_tokens
        row["output_tokens"] += cell.output_tokens
        row["cache_creation_tokens"] += cell.cache_creation_tokens
        row["cache_read_tokens"] += cell.cache_read_tokens
        row["cost"] += cell.cost
        row["models"].add(cell.model)
        row["projects"].add(cell.project)
    summaries = [
        DailySummary(
            date=d,
            input_tokens=r["input_tokens"],
            output_tokens=r["output_tokens"],
            cache_creation_tokens=r["cache_creation_tokens"],
            cache_read_tokens=r["cache_read_tokens"],
            cost=r["cost"],
            distinct_models=len(r["models"]),
            distinct_projects=len(r["projects"]),
        )
        for d, r in by_date.items()
    ]
    summaries.sort(key=lambda s: s.date, reverse=True)
    return summaries


def peak_day(summaries: Iterable[DailySummary]) -> tuple[str, float] | None:
    """`(date, cost)` of the highest-cost day in `summaries`, or
    `None` when the input is empty. Ties broken by latest date
    (more recent peak wins) — a deterministic rule a user can
    reason about when two days happen to cost the same.
    """
    materialised = list(summaries)
    if not materialised:
        return None
    top = max(materialised, key=lambda s: (s.cost, s.date))
    return top.date, top.cost


def active_days_count(summaries: Iterable[DailySummary]) -> int:
    """Number of days with at least one ccusage cell in the window.
    Equivalent to `len(summaries)` since `daily_summaries` only emits
    a row for dates that produced cells — the wrapper exists so the
    KPI card's denominator has a semantic name and the rule is
    testable in isolation.
    """
    return sum(1 for _ in summaries)


def avg_cost_per_active_day(summaries: Iterable[DailySummary]) -> float:
    """Total window cost divided by `active_days_count`. Zero on
    empty input (avoids ZeroDivisionError; the Daily renderer's
    empty-window branch short-circuits before this is shown).

    Distinct from `window_cost / window_days` (which the Overview
    KPI card surfaces): this metric weights only days that actually
    spent. A 30-day window with 5 active days at $20 each yields
    $20 here vs $3.33 on Overview.
    """
    materialised = list(summaries)
    if not materialised:
        return 0.0
    return sum(s.cost for s in materialised) / len(materialised)


def busiest_model(cells: Iterable[DailyCell]) -> tuple[str, float] | None:
    """`(model_name, share)` of the highest-cost model in the cell
    set, where `share` is the model's fraction of total cost
    (0.0 — 1.0). `None` when there are no cells.

    Aggregates cost per `model` (ignoring date and project) so the
    answer is window-wide, not per-day. Ties broken by model name
    descending — deterministic so two models with identical cost
    don't flicker between renders.
    """
    materialised = list(cells)
    if not materialised:
        return None
    cost_by_model: dict[str, float] = {}
    total = 0.0
    for c in materialised:
        cost_by_model[c.model] = cost_by_model.get(c.model, 0.0) + c.cost
        total += c.cost
    if total <= 0:
        return None
    top_name = max(cost_by_model, key=lambda m: (cost_by_model[m], m))
    return top_name, cost_by_model[top_name] / total


def daily_project_aggregates(cells: Iterable[DailyCell]) -> list[dict]:
    """Rollup `DailyCell`s per `(date, project)` for the Daily view's
    unified table. Each row carries:

        date:                     str (YYYY-MM-DD)
        project:                  str — raw ccusage slug, unformatted
                                   (renderer applies `project_display_name`)
        models:                   list[str] — raw model names that ran
                                   in this (date, project) bucket,
                                   sorted by descending per-model cost
                                   (ties broken by name descending)
        input_tokens:             int
        output_tokens:            int
        cache_creation_tokens:    int
        cache_read_tokens:        int
        total_tokens:             int
        cost:                     float

    Rows sorted by `date` descending (newest first); within a date,
    rows sorted by `cost` descending. Empty input → `[]`. Pure
    function — no I/O, no display formatting. Renderer is responsible
    for `display_model_label`, `project_display_name`, etc.
    """
    bucket: dict[tuple[str, str], dict] = {}
    for c in cells:
        key = (c.date, c.project)
        row = bucket.setdefault(
            key,
            {
                "date": c.date,
                "project": c.project,
                # Private: per-model cost map; collapsed into a sorted
                # `models` list at the end. Keeps the rollup loop
                # O(N) — we'd otherwise re-walk cells to compute model
                # ordering.
                "_models_cost": {},
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
            },
        )
        row["_models_cost"][c.model] = (
            row["_models_cost"].get(c.model, 0.0) + c.cost
        )
        row["input_tokens"] += c.input_tokens
        row["output_tokens"] += c.output_tokens
        row["cache_creation_tokens"] += c.cache_creation_tokens
        row["cache_read_tokens"] += c.cache_read_tokens
        row["total_tokens"] += c.total_tokens
        row["cost"] += c.cost
    rows: list[dict] = []
    for row in bucket.values():
        models_cost = row.pop("_models_cost")
        # Tie-break: lex-greater model name wins on equal cost — same
        # deterministic rule used by `busiest_model`, so two models
        # with identical cost never flicker between renders.
        row["models"] = sorted(
            models_cost, key=lambda m: (models_cost[m], m), reverse=True
        )
        rows.append(row)
    rows.sort(key=lambda r: (r["date"], r["cost"]), reverse=True)
    return rows


def cells_for_date(cells: Iterable[DailyCell], date_str: str) -> list[DailyCell]:
    """Return the subset of `cells` whose `date == date_str`, sorted by
    cost descending so the renderer iterates "where the money went"
    top-down — matches the Models breakdown table's sort rule.
    Stable secondary sort isn't required; ccusage emits at most one
    cell per `(date, model, project)` tuple so cost ties are rare
    and visually indistinguishable.
    """
    return sorted(
        (c for c in cells if c.date == date_str),
        key=lambda c: c.cost,
        reverse=True,
    )


def pluralize(count: int, singular: str) -> str:
    """English count + noun, with naive `-s` plural suffix. Used by the
    Daily view's day-row header (`2 models · 1 project`) so the
    pluralization rule lives in one place rather than being inlined
    everywhere a count needs a noun.

    Intentionally trivial: no exceptions for irregular plurals
    (`series`, `data`) because the dashboard's vocabulary is all
    regular nouns. Add a special-case table here if that ever
    changes — don't reinvent the rule at the call site.
    """
    return f"{count} {singular}" if count == 1 else f"{count} {singular}s"


def window_totals(cells: Iterable[DailyCell]) -> WindowTotals:
    """Window-wide rollup from `DailyCell` list. Empty input yields a
    zero-totals object rather than `None` so the Daily tab's totals
    card can always render with the same shape.
    """
    input_t = 0
    output_t = 0
    cache_c = 0
    cache_r = 0
    cost = 0.0
    for cell in cells:
        input_t += cell.input_tokens
        output_t += cell.output_tokens
        cache_c += cell.cache_creation_tokens
        cache_r += cell.cache_read_tokens
        cost += cell.cost
    return WindowTotals(
        input_tokens=input_t,
        output_tokens=output_t,
        cache_creation_tokens=cache_c,
        cache_read_tokens=cache_r,
        cost=cost,
    )


def display_model_label(model_name: str) -> str:
    """Human-readable model name for the Daily view: family
    capitalised, version digits joined with `.`, `claude-` prefix
    and the trailing YYYYMMDD date suffix both stripped.

    Examples:
        claude-opus-4-7            -> Opus 4.7
        claude-haiku-4-5-20251001  -> Haiku 4.5
        claude-sonnet-4-6          -> Sonnet 4.6
        claude-3-5-sonnet-20240620 -> Sonnet 3.5   (legacy ordering)
        claude-3-opus-20240229     -> Opus 3       (legacy ordering)
        gpt-4o                     -> gpt-4o       (no claude- prefix; passthrough)
        ""                         -> ""           (defensive)

    Delegates the date-suffix strip to `short_model_label` so the
    8-digit-YYYYMMDD rule lives in exactly one place. The
    family-detection logic (first all-alpha segment) handles both
    the modern `claude-<family>-<v>-<v>` ordering and the legacy
    `claude-<v>-<v>-<family>` ordering without a special-case table.
    """
    if not model_name or not model_name.startswith("claude-"):
        return model_name
    stripped = short_model_label(model_name)
    body = stripped[len("claude-"):]
    parts = body.split("-")
    family_idx = next(
        (i for i, p in enumerate(parts) if p.isalpha()),
        None,
    )
    if family_idx is None:
        # No alpha token to capitalise as the family. Pass the
        # date-stripped form through rather than mangling further.
        return stripped
    family = parts[family_idx].capitalize()
    version_segments = parts[:family_idx] + parts[family_idx + 1:]
    if not version_segments:
        return family
    return f"{family} {'.'.join(version_segments)}"


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


def round_money(amount: float) -> float:
    """Round a USD cost value to 2 decimal places (cents).

    Apply at the row-builder boundary so dataframe Cost cells carry
    clean 2-dp values for copy / sort / export. Streamlit's
    `NumberColumn(format="$%.2f")` rounds the *display*, but the
    underlying cell carries whatever value the row dict held — a raw
    IEEE float like `14.178827999999998` renders as `$14.18` while
    the clipboard still copies the noisy raw value. Rounding here
    fixes that without changing the rendered string.

    Python's `round()` uses banker's rounding (half-to-even). For
    money values arising from float arithmetic over upstream cents,
    the rounding direction at the half-cent boundary is functionally
    irrelevant: the input is float noise around a true value, not
    actual half-cents.

    Sibling of `format_compact_int` (compact tokens) and
    `format_money` (formatted USD string) — the three formatters
    live next to each other so the display rules for every numeric
    type the dashboard renders are findable in one place.
    """
    return round(amount, 2)


def format_money(amount: float) -> str:
    """Format a USD cost as ``$X,XXX.XX``.

    Combines `round_money` (clean 2-dp underlying value, so the
    output never carries IEEE noise from upstream arithmetic) with
    the ``$``-prefix + thousands-separator display rule. Use for any
    user-facing cost string that does NOT flow through Streamlit's
    `NumberColumn` formatter — `st.metric` values, captions,
    expander labels, embedded delta strings, etc.

    Inside a `NumberColumn` use `round_money` on the row value and
    let `NumberColumn(format="$%.2f")` apply the formatter — that
    keeps sorting / copy-paste numeric and avoids double-formatting.
    """
    return f"${round_money(amount):,.2f}"


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

