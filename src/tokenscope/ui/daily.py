"""Daily view — per-day expanders, each ending with its own Total row.

Surface (top-to-bottom):

  * `# Daily` H1 + window/timezone caption + agent-constraint caption
    (one page-level statement; no per-row chip).
  * Plan banner (when the active plan supplies one).
  * KPI strip (slice 5) — four bordered metric cards: peak day,
    active-days fraction, avg-per-active-day, busiest model.
  * One `st.expander` per active day (newest first, `expanded=True`
    by default). Label = day summary
    (`<date> · $<cost> · <tokens> tokens · <N model[s]> · <N project[s]>`).
    Contents: a `render_data_table` call carrying that day's
    per-project sub-rows followed by a final Total row aggregating
    those sub-rows. The day-total sits inside the per-day
    dataframe, not as a separate page-level dataframe — the reader
    sees the breakdown rows and their sum together without
    scrolling.

Single column config (`_PER_DAY_COLUMN_CONFIG`, 9 columns — no
Date) drives every dataframe on the surface. The date lives in
the expander label; carrying it in every row would be wasted
width. Same column widths + types across every dataframe by
construction.

DRY / SOLID anchors:

  - `ui/_tables.py:render_data_table` — single `st.dataframe`
    invocation. Same primitive Overview's Cost composition uses.
  - `paths.project_display_name` — single project-display rule;
    sidebar and Daily both consume it.
  - `analytics.daily_project_aggregates` — single per-(date,
    project) rollup. Pure function; renderer is a thin caller.
  - `_round_money` (private) — single money-rounding rule,
    applied at the row-builder boundary so dataframe Cost cells
    carry clean 2-dp values (NumberColumn formats `$X.XX` on top).
  - Module constants `_DAY_TOTAL_LABEL`, `_AGENT_BREAKDOWN_LABEL`,
    `AGENT_CONSTRAINT_CAPTION`, `_EMPTY_WINDOW_MESSAGE`,
    `KPI_LABEL_*` — every user-facing string lives in exactly
    one place.
"""

from __future__ import annotations

import streamlit as st

from tokenscope import config
from tokenscope.analytics import (
    DailyCell,
    DailySummary,
    active_days_count,
    avg_cost_per_active_day,
    busiest_model,
    daily_cells,
    daily_project_aggregates,
    daily_summaries,
    display_model_label,
    format_compact_int,
    format_timezone_for_display,
    peak_day,
    pluralize,
)
from tokenscope.navigation import Navigation
from tokenscope.paths import project_display_name
from tokenscope.ui._data import load_daily_by_project
from tokenscope.ui._tables import render_data_table
from tokenscope.ui.sidebar import SidebarState


# Agent constraint caption — every row in the dataset is Claude Code
# by construction (ccusage reads `~/.claude/projects/` JSONL only;
# SDK / console / third-party API traffic doesn't write there). The
# caption frames the constraint upfront so users don't mistake the
# uniform Agent column for a detection bug.
#
# Admin API ingestion (the real client-source-axis fix) is out of
# scope — see BACKLOG.md "Slice 28 — Anthropic Admin API ingestion".
AGENT_CONSTRAINT_CAPTION = (
    "All traffic is Claude Code — ccusage reads Claude Code "
    "transcripts only. SDK / console / third-party traffic is not "
    "visible here."
)

# Empty-window info copy — shared by both short-circuit paths in
# `render()` (no cells from ccusage / every cell filtered out by the
# zero-cost rule). Single source.
_EMPTY_WINDOW_MESSAGE = (
    "No usage in the selected window. Try widening the **Date "
    "range** in the sidebar, or clearing the **Project** filter "
    "if one is set."
)

# KPI card labels + tooltip — module-level constants so renderer copy
# and smoke-test assertions reference one source rather than racing
# string literals.
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

# Row-label constants. Every user-facing string in the table that
# isn't a function-call result lives in one of these — tests
# reference them by name, copy edits propagate from one place.
#
# (`_ALL_LABEL` from slice 8 was deleted along with `_all_row`:
# the day-aggregate row moved into the per-day expander's label.
# `_TOTAL_LABEL` was renamed to `_DAY_TOTAL_LABEL` after the
# standalone window-total dataframe was replaced by per-day Total
# rows; same string value, narrower scope.)
_DAY_TOTAL_LABEL = "Total"
# Leading dash mirrors ccusage's sub-row indent convention. The
# "Claude Code" suffix keeps the agent label consistent with our
# app's terminology (vs ccusage's bare "Claude" abbreviation) AND
# with `AGENT_CONSTRAINT_CAPTION` above.
_AGENT_BREAKDOWN_LABEL = "- Claude Code"

# Column-config for the Daily view's per-day dataframes. Same
# column-type pattern as Overview's Cost-composition table —
# TextColumn for label/token columns carrying pre-formatted strings,
# NumberColumn for cost, explicit `width` on every column.
#
# No Date column: the date lives in the expander label, so a Date
# column on every row would be blank and waste screen width. The
# day's aggregate Total row appears INSIDE each per-day dataframe
# as its last row, not as a separate dataframe with its own Date
# column — so a single 9-column config drives every dataframe on
# the Daily surface.
_PER_DAY_COLUMN_CONFIG: dict = {
    "Agent":        st.column_config.TextColumn(width="small"),
    "Project":      st.column_config.TextColumn(width="medium"),
    "Models":       st.column_config.TextColumn(width="medium"),
    "Input":        st.column_config.TextColumn(width="small"),
    "Output":       st.column_config.TextColumn(width="small"),
    "Cache create": st.column_config.TextColumn(width="small"),
    "Cache read":   st.column_config.TextColumn(width="small"),
    "Total tokens": st.column_config.TextColumn(width="small"),
    "Cost":         st.column_config.NumberColumn(format="$%.2f", width="medium"),
}

# Public for tests — the column-order contract smoke tests pin.
PER_DAY_COLUMNS: tuple[str, ...] = tuple(_PER_DAY_COLUMN_CONFIG.keys())


def render(state: SidebarState, nav: Navigation) -> None:
    """Daily tab entry-point. Renders KPI strip + single unified
    table. No expanders. Mirrors the `render(state, nav)` signature
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
    # Zero-cost days are silently dropped. ccusage doesn't currently
    # emit them, but if it ever does they'd render as a `$0.00` row
    # carrying no useful signal. The cells list is narrowed to the
    # surviving dates so `daily_project_aggregates` and
    # `busiest_model` stay consistent with `summaries`.
    summaries = [s for s in daily_summaries(cells) if s.cost > 0]
    if not summaries:
        st.info(_EMPTY_WINDOW_MESSAGE)
        return
    active_dates = {s.date for s in summaries}
    cells = [c for c in cells if c.date in active_dates]

    window_days = state.query.window_days() or config.DEFAULT_RANGE_DAYS
    _render_kpi_strip(summaries, cells, window_days)
    _render_unified_table(summaries, cells)


def _render_page_header(state: SidebarState) -> None:
    """H1 + window/timezone caption + agent-constraint caption. The
    second caption sits inline with the window/tz line as a sibling
    sub-line — same `st.caption` weight, so the user reads them as
    one block of context rather than as a banner/warning. Copy is
    `AGENT_CONSTRAINT_CAPTION` (module-level) — one place."""
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
    """Four Daily-specific KPI cards. Each answers a question the
    Overview tab doesn't:

      1. Peak day                — most expensive day in the window
                                   (date + cost).
      2. Active days             — `N / window_days` — how
                                   concentrated the spend was.
      3. Avg per active day      — cost ÷ active days; help tooltip
                                   spells out the distinction from
                                   Overview's `Avg daily cost`.
      4. Busiest model           — top-cost model window-wide + its
                                   share of spend.

    Inputs are the already-filtered `summaries` / `cells` from
    `render()`. None-handling on `peak_day` / `busiest_model` is
    defensive — the render() empty-window branch short-circuits
    before this strip is shown, but the `is None` arms below render
    `—` rather than crashing if reached via monkeypatch paths.
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
            st.metric(KPI_LABEL_PEAK_DAY, f"${cost:,.2f}")
            st.caption(f"on {date}")

    with c2, st.container(border=True):
        st.metric(KPI_LABEL_ACTIVE_DAYS, f"{active} / {window_days}")
        st.caption("days with activity")

    with c3, st.container(border=True):
        st.metric(
            KPI_LABEL_AVG_PER_ACTIVE_DAY,
            f"${avg_active:,.2f}",
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


def _round_money(amount: float) -> float:
    """Round a cost value to 2 decimal places (USD cents convention).

    Used at the row-builder boundary so dataframe Cost cells carry
    clean 2-dp values for copy / sort / export, while Streamlit's
    `NumberColumn(format="$%.2f")` still applies the currency
    formatter on top.

    Without this, a raw IEEE float like `14.178827999999998` renders
    as `$14.18` (NumberColumn rounds the display) but the underlying
    cell carries the noisy raw value — copying a column to the
    clipboard or sorting numerically exposes the noise.

    Python's `round()` uses banker's rounding (half-to-even). For
    money values arising from float arithmetic over upstream cents,
    the rounding direction at the half-cent boundary is functionally
    irrelevant: the input is float noise around a true value, not
    actual half-cents.
    """
    return round(amount, 2)


def _render_unified_table(
    summaries: list[DailySummary],
    cells: list[DailyCell],
) -> None:
    """Render the Daily surface as N per-day `st.expander`s (newest
    day first). Each expander's label carries the day's summary
    (date · cost · tokens · model count · project count); the
    expander contents are a single `render_data_table` call with
    that day's per-project sub-rows followed by a Total row
    aggregating those rows.

    The day's Total row is the last row of the per-day dataframe —
    not a separate dataframe — so the reader sees the breakdown
    rows and their sum side-by-side without scrolling to a page-
    level total. Every dataframe on the Daily surface uses the
    same `_PER_DAY_COLUMN_CONFIG`; column widths and types are
    identical by construction.
    """
    summary_by_date = {s.date: s for s in summaries}
    project_rows_by_date: dict[str, list[dict]] = {}
    for row in daily_project_aggregates(cells):
        project_rows_by_date.setdefault(row["date"], []).append(row)

    for date in sorted(summary_by_date, reverse=True):
        summary = summary_by_date[date]
        day_rows = [
            _project_sub_row(r) for r in project_rows_by_date.get(date, [])
        ]
        day_rows.append(_day_total_row(summary))
        with st.expander(_day_expander_label(summary), expanded=True):
            render_data_table(day_rows, _PER_DAY_COLUMN_CONFIG)


def _day_expander_label(summary: DailySummary) -> str:
    """Label for the per-day expander. Format:
    `<date> · $<cost> · <tokens> tokens · <N model[s]> · <N project[s]>`.

    Order is scan-optimised: cost second (the field users come here
    to compare), tokens third, model/project counts last. The cost
    formatter matches the dataframe Cost column's display format
    (`$X.XX`) so the header and the column never read with different
    money precision.
    """
    return (
        f"{summary.date} · "
        f"${_round_money(summary.cost):,.2f} · "
        f"{format_compact_int(summary.total_tokens)} tokens · "
        f"{pluralize(summary.distinct_models, 'model')} · "
        f"{pluralize(summary.distinct_projects, 'project')}"
    )


def _project_sub_row(project_row: dict) -> dict:
    """Per-project breakdown row inside a day's expander. No Date
    column (date is the expander label). Agent has the leading-dash
    indent marker; Project is the resolved display name; Models is
    the `, `-joined model labels for this (date, project) bucket in
    per-model cost-desc order; Cost is money-rounded for clean
    underlying cell values."""
    return {
        "Agent": _AGENT_BREAKDOWN_LABEL,
        "Project": project_display_name(project_row["project"]),
        "Models": ", ".join(
            display_model_label(m) for m in project_row["models"]
        ),
        "Input": format_compact_int(project_row["input_tokens"]),
        "Output": format_compact_int(project_row["output_tokens"]),
        "Cache create": format_compact_int(project_row["cache_creation_tokens"]),
        "Cache read": format_compact_int(project_row["cache_read_tokens"]),
        "Total tokens": format_compact_int(project_row["total_tokens"]),
        "Cost": _round_money(project_row["cost"]),
    }


def _day_total_row(summary: DailySummary) -> dict:
    """Final row inside a day's per-day dataframe. Aggregates the
    project sub-rows above it — same `DailySummary` data the
    expander label carries, surfaced inside the dataframe so the
    user reads the day total in-context with the breakdown rows
    rather than scrolling to a page-level total.

    Agent = `_DAY_TOTAL_LABEL` ("Total"); Project / Models blank
    (the aggregate spans all projects and all models for the day);
    numerics from `DailySummary` via the same `format_compact_int`
    / `_round_money` helpers the project sub-rows use, so header
    cost, project-row numerics, and day-total numerics can't drift
    on formatting.
    """
    return {
        "Agent": _DAY_TOTAL_LABEL,
        "Project": "",
        "Models": "",
        "Input": format_compact_int(summary.input_tokens),
        "Output": format_compact_int(summary.output_tokens),
        "Cache create": format_compact_int(summary.cache_creation_tokens),
        "Cache read": format_compact_int(summary.cache_read_tokens),
        "Total tokens": format_compact_int(summary.total_tokens),
        "Cost": _round_money(summary.cost),
    }
