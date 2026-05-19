"""Daily view — per-day breakdown matching ccusage's daily report layout.

Slice 2 ships the window-totals card only (header + bordered metric
strip). Slice 3 adds the per-day expanders below this card, each
containing a `(model, project)` sub-table that iterates the same
`_COLUMNS` tuple defined here — single source of truth for column
order, display label, attribute name, and formatting rule.

The Daily tab does not introduce charts; ccusage parity is the goal,
and ccusage's daily report is a table. Visual language is matched to
Overview / Models / Cache: `# Daily` H1, window subtitle caption,
plan banner when set, empty-window info message, then bordered
`st.metric` cards in `st.columns(len(_COLUMNS))`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import streamlit as st

from tokenscope import config
from tokenscope.analytics import (
    WindowTotals,
    daily_cells,
    format_compact_int,
    format_timezone_for_display,
    window_totals,
)
from tokenscope.navigation import Navigation
from tokenscope.ui._data import load_daily_by_project
from tokenscope.ui.sidebar import SidebarState


@dataclass(frozen=True, slots=True)
class _ColumnSpec:
    """One column of the Daily view's row grid.

    `attr` is the field name read off `WindowTotals` (this slice) and
    off `DailySummary` / per-`(model, project)` rows in slice 3. The
    same tuple `_COLUMNS` drives every render path so column order,
    labels, and formats live in exactly one place.
    """

    label: str
    attr: str
    formatter: Callable[[int | float], str]


def _fmt_tokens(value: int | float) -> str:
    """Compact integer formatting for token counts (e.g. 1.37B)."""
    return format_compact_int(int(value))


def _fmt_cost(value: int | float) -> str:
    """USD formatting for cost values."""
    return f"${value:,.2f}"


# Single source of truth for the Daily view's column grid. The totals
# card here iterates this tuple; slice 3's per-day sub-table iterates
# the same tuple against `DailySummary` / per-`(model, project)` row
# objects. Adding or reordering a column requires one change here —
# no parallel literal lists in two render paths to drift.
_COLUMNS: tuple[_ColumnSpec, ...] = (
    _ColumnSpec("Input", "input_tokens", _fmt_tokens),
    _ColumnSpec("Output", "output_tokens", _fmt_tokens),
    _ColumnSpec("Cache create", "cache_creation_tokens", _fmt_tokens),
    _ColumnSpec("Cache read", "cache_read_tokens", _fmt_tokens),
    _ColumnSpec("Total tokens", "total_tokens", _fmt_tokens),
    _ColumnSpec("Cost", "cost", _fmt_cost),
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

    _render_totals_card(window_totals(cells))


def _render_page_header(state: SidebarState) -> None:
    """H1 + window/timezone caption — copy and idiom match the
    Overview / Models headers exactly."""
    window_days = state.query.window_days() or config.DEFAULT_RANGE_DAYS
    tz_display = format_timezone_for_display(state.query.tz or "")
    st.markdown("# Daily")
    st.caption(f"Window: last {window_days} days · times in {tz_display}")


def _render_totals_card(totals: WindowTotals) -> None:
    """Window-wide totals — one bordered `st.metric` per column in
    `st.columns(len(_COLUMNS))`. Matches the Overview / Models KPI
    strip pattern.
    """
    cols = st.columns(len(_COLUMNS))
    for col, spec in zip(cols, _COLUMNS):
        with col, st.container(border=True):
            st.metric(spec.label, spec.formatter(getattr(totals, spec.attr)))
