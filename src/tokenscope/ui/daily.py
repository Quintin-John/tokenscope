"""Daily view — per-day breakdown matching ccusage's daily report layout.

Surface (top-to-bottom):

  * `# Daily` H1 + window/timezone caption + agent-constraint caption.
  * Plan banner (when the active plan supplies one).
  * KPI strip (slice 5) — four bordered metric cards answering
    questions Overview doesn't: peak day, active-days fraction,
    avg-per-active-day, busiest model.
  * Per-day expanders (slice 3) — one expander per date in the
    window, descending order. The collapsed header carries the
    day's totals so the user can scan without expanding. Expanded,
    each shows a `(model, project)` sub-table sorted by cost desc.

Slice 5 replaced the window-totals strip (slice 2) with the four
KPI cards above. The cost/tokens-by-kind window-wide rollup that
strip surfaced already lives on Overview's Cost-composition table —
duplicating it here added nothing Daily-specific. The KPI strip
answers per-day-distribution questions Overview can't.

Single source of truth for the per-day sub-table grid:
`_COLUMNS` (numeric columns) and `_DAY_SUBTABLE_COLUMNS = (model,
project, *_COLUMNS)`. Both are tuples of `_ColumnSpec(label, attr,
kind)`; the `kind` discriminator drives formatting and the
`st.column_config` rule. One column-grid definition, one render
context now (sub-table only after slice 5), no parallel literal
lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import streamlit as st

from tokenscope import config
from tokenscope.analytics import (
    DailyCell,
    DailySummary,
    active_days_count,
    avg_cost_per_active_day,
    busiest_model,
    cells_for_date,
    daily_cells,
    daily_summaries,
    display_model_label,
    format_compact_int,
    format_timezone_for_display,
    peak_day,
    pluralize,
    project_basename,
)
from tokenscope.navigation import Navigation
from tokenscope.ui._data import load_daily_by_project
from tokenscope.ui.sidebar import SidebarState


# Agent label and constraint caption — single source of truth for the
# "what client made these requests" surface. Every row in the dataset
# is Claude Code by construction (ccusage reads `~/.claude/projects/`
# JSONL only; SDK / console / third-party API traffic doesn't write
# there). The chip in the day-row header surfaces this so the
# constant value is explained, not silently omitted. The caption
# under the page subtitle frames the constraint upfront so users
# don't mistake the uniformity for a detection bug.
#
# Admin API ingestion (the real "differentiate Claude Code vs SDK
# vs console vs third-party" path) is intentionally not in scope —
# see BACKLOG.md "Slice 28 — Anthropic Admin API ingestion".
AGENT_LABEL = "Claude Code"

AGENT_CONSTRAINT_CAPTION = (
    "All traffic is Claude Code — ccusage reads Claude Code "
    "transcripts only. SDK / console / third-party traffic is not "
    "visible here."
)


# KPI card labels and tooltip — module-level constants so the
# renderer copy and smoke-test assertions reference one source
# rather than racing string literals. Each KPI answers a question
# the Overview tab doesn't answer:
#
#   Peak day                 — date + cost of the most expensive
#                              day in the window.
#   Active days              — N / window_days; how concentrated
#                              the spend was in time.
#   Avg per active day       — cost ÷ active days. Distinct from
#                              Overview's "Avg daily cost" which
#                              divides by window length. Tooltip
#                              spells out the denominator.
#   Busiest model            — top-cost model window-wide + its
#                              share of total spend. Pairs with
#                              the Claude Code chip; saves a click
#                              into the Models tab.
KPI_LABEL_PEAK_DAY = "Peak day"
KPI_LABEL_ACTIVE_DAYS = "Active days"
KPI_LABEL_AVG_PER_ACTIVE_DAY = "Avg per active day"
KPI_LABEL_BUSIEST_MODEL = "Busiest model"

KPI_HELP_AVG_PER_ACTIVE_DAY = (
    "Total window cost divided by days that actually had activity, "
    "NOT by window length. The Overview tab's `Avg daily cost` "
    "divides by window length instead — use that to compare windows "
    "of different lengths; use this to weight by busy days only."
)


# `kind` discriminates how the renderer pulls a value out of the
# source object (`WindowTotals` / `DailyCell`) and how it presents
# it. "tokens" / "cost" / "model" / "project" are the only kinds
# the Daily view needs; introducing a new kind requires extending
# both `_metric_value` and `_subtable_value` (and the test surface).
_ColumnKind = Literal["tokens", "cost", "model", "project"]


@dataclass(frozen=True, slots=True)
class _ColumnSpec:
    """One column of the Daily view's row grid.

    `attr` names the field on the source object (`WindowTotals` for
    the totals card; `DailyCell` for the sub-table). `kind` selects
    the formatting rule — see `_metric_value` and `_subtable_value`.
    No format callbacks live on the spec itself: a single dispatch
    function per render context owns the kind→format mapping, so
    the rule cannot drift between the totals card and the sub-table.
    """

    label: str
    attr: str
    kind: _ColumnKind


def _fmt_tokens(value: int | float) -> str:
    """Compact integer formatting for token counts (e.g. 1.37B)."""
    return format_compact_int(int(value))


def _fmt_cost(value: int | float) -> str:
    """USD formatting for cost values."""
    return f"${value:,.2f}"


# Numeric-only columns — shared by the totals card and the sub-table.
_COLUMNS: tuple[_ColumnSpec, ...] = (
    _ColumnSpec("Input", "input_tokens", "tokens"),
    _ColumnSpec("Output", "output_tokens", "tokens"),
    _ColumnSpec("Cache create", "cache_creation_tokens", "tokens"),
    _ColumnSpec("Cache read", "cache_read_tokens", "tokens"),
    _ColumnSpec("Total tokens", "total_tokens", "tokens"),
    _ColumnSpec("Cost", "cost", "cost"),
)


# Sub-table columns — the numeric columns above prefixed with Model
# and Project labels. The prefix order is fixed (label columns first)
# so the table reads "what · where · how much" left-to-right.
_DAY_SUBTABLE_COLUMNS: tuple[_ColumnSpec, ...] = (
    _ColumnSpec("Model", "model", "model"),
    _ColumnSpec("Project", "project", "project"),
    *_COLUMNS,
)


def render(state: SidebarState, nav: Navigation) -> None:
    """Daily tab entry-point. Mirrors the `render(state, nav)` signature
    every other top-level view uses so `app._RENDERERS` can dispatch
    uniformly."""
    _render_page_header(state)

    banner = state.plan.banner_text()
    if banner is not None:
        st.info(banner)

    report = load_daily_by_project(state)
    if report is None:
        return

    cells = daily_cells(report)
    if not cells:
        st.info(
            "No usage in the selected window. Try widening the **Date "
            "range** in the sidebar, or clearing the **Project** filter "
            "if one is set."
        )
        return

    summaries = daily_summaries(cells)
    window_days = state.query.window_days() or config.DEFAULT_RANGE_DAYS
    _render_kpi_strip(summaries, cells, window_days)
    _render_day_rows(cells, summaries)


def _render_page_header(state: SidebarState) -> None:
    """H1 + window/timezone caption + agent-constraint caption. The
    second caption sits inline with the window/tz line as a sibling
    sub-line — same `st.caption` weight, so the user reads them as
    one block of context rather than as a banner / warning. Copy is
    `AGENT_CONSTRAINT_CAPTION` (module-level constant) so the
    constraint statement lives in exactly one place.
    """
    window_days = state.query.window_days() or config.DEFAULT_RANGE_DAYS
    tz_display = format_timezone_for_display(state.query.tz or "")
    st.markdown("# Daily")
    st.caption(f"Window: last {window_days} days · times in {tz_display}")
    st.caption(AGENT_CONSTRAINT_CAPTION)


def _render_kpi_strip(
    summaries: list[DailySummary],
    cells: list[DailyCell],
    window_days: int,
) -> None:
    """Four Daily-specific KPI cards in a bordered row. Each card
    answers a question the Overview tab doesn't:

      1. Peak day                — the most expensive day in the
                                   window (date + cost).
      2. Active days             — `N / window_days`; how
                                   concentrated the spend was.
      3. Avg per active day      — cost ÷ active days. Tooltip
                                   spells out the distinction from
                                   Overview's `Avg daily cost`
                                   (which divides by window length).
      4. Busiest model           — top-cost model across the window
                                   + its share of spend. Pairs
                                   with the Claude Code agent chip.

    Inputs are the already-computed `summaries` / `cells` from
    `render()`; no data refetch.

    None-handling: `peak_day` and `busiest_model` return None on
    empty input. The renderer's empty-window branch short-circuits
    before this strip is shown, so the `is None` branches below
    are defensive (e.g. fixture / monkeypatch paths) — they render
    `—` with a "no activity" caption rather than crashing.
    """
    peak = peak_day(summaries)
    active = active_days_count(summaries)
    avg_active = avg_cost_per_active_day(summaries)
    busiest = busiest_model(cells)

    c1, c2, c3, c4 = st.columns(4)

    with c1, st.container(border=True):
        if peak is None:
            st.metric(KPI_LABEL_PEAK_DAY, "—")
            st.caption("no activity in window")
        else:
            date, cost = peak
            st.metric(KPI_LABEL_PEAK_DAY, _fmt_cost(cost))
            st.caption(f"on {date}")

    with c2, st.container(border=True):
        st.metric(KPI_LABEL_ACTIVE_DAYS, f"{active} / {window_days}")
        st.caption("days with activity")

    with c3, st.container(border=True):
        st.metric(
            KPI_LABEL_AVG_PER_ACTIVE_DAY,
            _fmt_cost(avg_active),
            help=KPI_HELP_AVG_PER_ACTIVE_DAY,
        )
        st.caption(f"across {pluralize(active, 'active day')}")

    with c4, st.container(border=True):
        if busiest is None:
            st.metric(KPI_LABEL_BUSIEST_MODEL, "—")
            st.caption("no models in window")
        else:
            model, share = busiest
            st.metric(KPI_LABEL_BUSIEST_MODEL, display_model_label(model))
            st.caption(f"{share:.1%} of window spend")


def _render_day_rows(
    cells: list[DailyCell], summaries: list[DailySummary]
) -> None:
    """One `st.expander` per day in descending date order. Expanders
    open by default — manually clicking each one to inspect a day's
    breakdown is friction without a payoff (the header carries the
    summary fields, but the sub-table is what the user came here for).
    Streamlit preserves the user's collapse choice across reruns
    within a session, so anyone who wants the scan-only view can
    collapse a row once and it stays collapsed.

    A thin magnitude bar sits ABOVE each expander (not inside) so
    it stays visible regardless of the expander's state — a glance
    reveals where the heavy days are without reading the cost on
    every header. The bar fill is scaled to the day's share of the
    peak-day cost; peak day = full fill.
    """
    max_cost = max((s.cost for s in summaries), default=0.0)
    for summary in summaries:
        _render_day_magnitude_bar(summary, max_cost)
        with st.expander(_day_header(summary), expanded=True):
            _render_day_subtable(summary, cells_for_date(cells, summary.date))


def _render_day_magnitude_bar(summary: DailySummary, max_cost: float) -> None:
    """Magnitude bar above each day-row expander. Fill width =
    `day_cost / max_day_cost` (clamped to [0, 1]). The styling lives
    in `_app_styles.css` (`.tokenscope-daily-day-bar` /
    `.tokenscope-daily-day-bar-fill`); only the dynamic width sits
    inline.

    Defensive zero-handling: when every day in the window costs zero
    (rare; the renderer's empty-window branch usually short-circuits
    first), `max_cost == 0` and every bar renders empty rather than
    triggering a divide-by-zero.
    """
    if max_cost > 0:
        share = max(0.0, min(1.0, summary.cost / max_cost))
    else:
        share = 0.0
    st.markdown(
        f'<div class="tokenscope-daily-day-bar">'
        f'<div class="tokenscope-daily-day-bar-fill" '
        f'style="width: {share * 100:.2f}%"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _day_header(summary: DailySummary) -> str:
    """Collapsed-state expander header. Format:
    `<date> · <cost> · <tokens> tokens · <N models> · <N projects> · <agent>`.

    Order is scan-optimised: cost second (the field users come here
    to compare), tokens third, model/project counts fourth/fifth,
    agent chip last (constant — every row reads `Claude Code` so
    placing it last keeps the variable fields scannable on the left).

    Uses the same `_fmt_tokens` / `_fmt_cost` helpers as the totals
    card and the same `AGENT_LABEL` constant as `_render_page_header`'s
    caption — header text, totals strip, and constraint caption all
    share single sources of truth.
    """
    return (
        f"{summary.date} · "
        f"{_fmt_cost(summary.cost)} · "
        f"{_fmt_tokens(summary.total_tokens)} tokens · "
        f"{pluralize(summary.distinct_models, 'model')} · "
        f"{pluralize(summary.distinct_projects, 'project')} · "
        f"{AGENT_LABEL}"
    )


def _columns_for_day(distinct_projects: int) -> tuple[_ColumnSpec, ...]:
    """Per-day subset of `_DAY_SUBTABLE_COLUMNS`. Drops the Project
    column when only one project ran that day — the expander header
    already reads `1 project`, so the column would repeat the same
    string in every row. Kept when 2+ projects are present so the
    user can see which project drove which cost.
    """
    if distinct_projects > 1:
        return _DAY_SUBTABLE_COLUMNS
    return tuple(c for c in _DAY_SUBTABLE_COLUMNS if c.kind != "project")


def _render_day_subtable(
    summary: DailySummary, day_cells: list[DailyCell]
) -> None:
    """`(model, project)` rows for one day. Sorted by cost desc by
    `cells_for_date`. Numeric token / cost columns reach the
    dataframe as raw numbers; `column_config` formats them and
    Streamlit right-aligns numeric columns automatically. The Project
    column is dropped on single-project days (see `_columns_for_day`).
    """
    columns = _columns_for_day(summary.distinct_projects)
    rows = [
        {spec.label: _subtable_value(spec, cell) for spec in columns}
        for cell in day_cells
    ]
    column_config = {
        spec.label: cc
        for spec in columns
        if (cc := _subtable_column_config(spec)) is not None
    }
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        column_config=column_config,
    )


def _subtable_value(
    spec: _ColumnSpec, cell: DailyCell
) -> str | int | float:
    """Format a `DailyCell` field for the per-day sub-table.

    Numeric kinds (`tokens`, `cost`) return raw values; the
    corresponding `NumberColumn` entries in `_subtable_column_config`
    format them and Streamlit right-aligns numeric columns by default.
    Label kinds (`model`, `project`) return pre-formatted strings
    from `display_model_label` / `project_basename` — those helpers
    live in `analytics.py` so the Daily view doesn't introduce a
    parallel display rule.
    """
    value = getattr(cell, spec.attr)
    if spec.kind == "tokens":
        return int(value)
    if spec.kind == "cost":
        return value
    if spec.kind == "model":
        return display_model_label(value)
    if spec.kind == "project":
        return project_basename(value)
    raise ValueError(f"_subtable_value: unknown kind {spec.kind!r}")


_PROJECT_COLUMN_HELP = (
    "Last path segment of the project directory. Lossy when a repo "
    "name contains hyphens (ccusage encodes path separators as `-`) "
    "— the sidebar's Project dropdown carries the full slug for "
    "disambiguation."
)


def _subtable_column_config(spec: _ColumnSpec):
    """Per-column `st.column_config` entry. Numeric columns get
    `NumberColumn` (Streamlit right-aligns those automatically):
    cost → `$%.2f`, tokens → `localized` (`1,374,041,578` with comma
    separators). The Project column gets a `TextColumn` with column-
    level help explaining the basename heuristic. Model column falls
    through to Streamlit's default `TextColumn` (no help needed —
    the display values are unambiguous).
    """
    if spec.kind == "cost":
        return st.column_config.NumberColumn(format="$%.2f")
    if spec.kind == "tokens":
        return st.column_config.NumberColumn(format="localized")
    if spec.kind == "project":
        return st.column_config.TextColumn(help=_PROJECT_COLUMN_HELP)
    return None
