"""Daily view — single unified table matching Overview's Cost-composition
visual language, with per-day breakdown rows interleaved.

Surface (top-to-bottom):

  * `# Daily` H1 + window/timezone caption + agent-constraint caption
    (one page-level statement; no per-row chip).
  * Plan banner (when the active plan supplies one).
  * KPI strip (slice 5) — four bordered metric cards: peak day,
    active-days fraction, avg-per-active-day, busiest model.
  * One unified `st.dataframe` rendered via `render_data_table` —
    same primitive Overview's Cost composition uses, same fonts,
    widths, alignment by construction.

Unified table shape (10 columns):

    Date · Agent · Project · Models · Input · Output ·
    Cache create · Cache read · Total tokens · Cost

Row structure, descending date order (newest day at top):

  - "All" row per day:        Date filled, Agent="All", Project blank,
                              Models blank, numeric columns = day's
                              aggregate sums across every cell.
  - Project sub-row(s):       Date blank, Agent="- Claude Code",
                              Project = `project_display_name(slug)`,
                              Models = `, `-joined `display_model_label`
                              outputs in per-(date, project, model)
                              cost-descending order, numerics = the
                              project's day total summed across models.
                              One sub-row per project that ran that day.
  - "Total" row at the bottom: Date="Total", Agent blank, Project
                              blank, Models blank, numerics = window-
                              wide sums.

Column-config mirrors Overview Cost composition exactly: TextColumn
for every label/token column carrying pre-formatted strings, Cost is
the one NumberColumn. Explicit `width` on every column — no
auto-detect. That's the DRY/SOLID anchor: one render primitive
(`render_data_table`), one project-display rule
(`paths.project_display_name`), one model-display rule
(`analytics.display_model_label`), one per-(date, project) rollup
(`analytics.daily_project_aggregates`).
"""

from __future__ import annotations

import streamlit as st

from tokenscope import config
from tokenscope.analytics import (
    DailyCell,
    DailySummary,
    WindowTotals,
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
    window_totals,
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

# Unified-table row-type labels and agent indent marker. Every
# user-facing string in the table that isn't a function-call result
# lives in one of these constants. Tests reference these by name —
# a copy edit propagates from one place.
_ALL_LABEL = "All"
_TOTAL_LABEL = "Total"
# Leading dash mirrors ccusage's sub-row indent convention. The
# "Claude Code" suffix keeps the agent label consistent with our
# app's terminology (vs ccusage's bare "Claude" abbreviation) AND
# with `AGENT_CONSTRAINT_CAPTION` above.
_AGENT_BREAKDOWN_LABEL = "- Claude Code"

# Column-config for the unified table. Same column-type pattern as
# Overview's Cost-composition table (TextColumn for label/token
# columns carrying pre-formatted strings, NumberColumn for cost,
# explicit width on every column). The visual language is shared by
# construction — Streamlit's render path for these column types is
# identical between the two consumers.
_TABLE_COLUMN_CONFIG: dict = {
    "Date":         st.column_config.TextColumn(width="small"),
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

# Public for tests — the column-order contract that smoke tests pin.
TABLE_COLUMNS: tuple[str, ...] = tuple(_TABLE_COLUMN_CONFIG.keys())


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
    _render_unified_table(summaries, cells, window_totals(cells))


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


def _render_unified_table(
    summaries: list[DailySummary],
    cells: list[DailyCell],
    totals: WindowTotals,
) -> None:
    """Build the table rows (per-day All + per-project sub-rows + one
    Total row) and hand off to `render_data_table`. The render
    primitive is shared with Overview's Cost composition — same
    Streamlit invocation, same fonts/widths/alignment by
    construction."""
    summary_by_date = {s.date: s for s in summaries}
    project_rows = daily_project_aggregates(cells)
    project_rows_by_date: dict[str, list[dict]] = {}
    for row in project_rows:
        project_rows_by_date.setdefault(row["date"], []).append(row)

    table_rows: list[dict] = []
    for date in sorted(summary_by_date, reverse=True):
        table_rows.append(_all_row(summary_by_date[date]))
        for project_row in project_rows_by_date.get(date, []):
            table_rows.append(_project_sub_row(project_row))
    table_rows.append(_total_row(totals))

    render_data_table(table_rows, _TABLE_COLUMN_CONFIG)


def _all_row(summary: DailySummary) -> dict:
    """Aggregate row for one day. Date filled with the date string;
    Agent = `All`; Project / Models blank; numeric columns carry the
    day's aggregate sums."""
    return {
        "Date": summary.date,
        "Agent": _ALL_LABEL,
        "Project": "",
        "Models": "",
        "Input": format_compact_int(summary.input_tokens),
        "Output": format_compact_int(summary.output_tokens),
        "Cache create": format_compact_int(summary.cache_creation_tokens),
        "Cache read": format_compact_int(summary.cache_read_tokens),
        "Total tokens": format_compact_int(summary.total_tokens),
        "Cost": summary.cost,
    }


def _project_sub_row(project_row: dict) -> dict:
    """Per-project breakdown row under a day's All row. Date blank
    (visual cue that this row continues the day above); Agent has
    the leading-dash indent marker; Project is the resolved display
    name; Models is the `, `-joined model labels for this
    (date, project) bucket in per-model cost-desc order."""
    return {
        "Date": "",
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
        "Cost": project_row["cost"],
    }


def _total_row(totals: WindowTotals) -> dict:
    """Final window-wide totals row. Date = "Total" literal;
    Agent / Project / Models blank; numerics are the window sums."""
    return {
        "Date": _TOTAL_LABEL,
        "Agent": "",
        "Project": "",
        "Models": "",
        "Input": format_compact_int(totals.input_tokens),
        "Output": format_compact_int(totals.output_tokens),
        "Cache create": format_compact_int(totals.cache_creation_tokens),
        "Cache read": format_compact_int(totals.cache_read_tokens),
        "Total tokens": format_compact_int(totals.total_tokens),
        "Cost": totals.cost,
    }
