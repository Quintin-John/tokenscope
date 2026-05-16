"""Overview page: KPIs + three charts. Click-to-drill on the stacked area.

Per PLAN.md §3.3 the stacked-area chart is the entry point to the day
view — clicking a day in the chart sets `?view=day&day=YYYY-MM-DD` and
reruns. The model filter from the sidebar is applied post-fetch via
`filter_daily_by_models`.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    active_block_burn,
    aggregate_cache_hit_ratio,
    available_models,
    filter_daily_by_models,
    mtd_cost,
    today_cost,
)
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.ui.charts import (
    rolling_average_line,
    stacked_area_cost_by_family,
    token_mix_bar,
)
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation, today: date | None = None) -> None:
    today = today or date.today()

    if (banner := state.plan.banner_text()) is not None:
        st.info(banner)

    try:
        daily_report = data.daily(state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    # Apply post-fetch model filter — only when user has narrowed below "all".
    all_models = available_models(daily_report)
    chosen = set(state.selected_models)
    if chosen and chosen != set(all_models):
        daily_report = filter_daily_by_models(daily_report, chosen)

    try:
        blocks_report = data.blocks(active=True, query=state.query)
    except CcusageError:
        blocks_report = None

    _render_kpis(daily_report, blocks_report, today)
    st.divider()

    if not daily_report.daily:
        st.caption("No usage in the selected window.")
        return

    st.subheader("Daily cost by model family")
    fig = stacked_area_cost_by_family(daily_report)
    if fig is not None:
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-stacked-area",
            on_select="rerun",
            selection_mode=("points",),
        )
        _handle_day_click(event, nav)

    st.subheader("7-day rolling average cost")
    fig = rolling_average_line(daily_report, window_days=7)
    if fig is not None:
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-rolling-line",
            on_select="rerun",
            selection_mode=("points",),
        )
        _handle_day_click(event, nav)

    st.subheader("Daily token mix")
    fig = token_mix_bar(daily_report)
    if fig is not None:
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-token-mix",
            on_select="rerun",
            selection_mode=("points",),
        )
        _handle_day_click(event, nav)


def _render_kpis(daily_report, blocks_report, today: date) -> None:
    mtd = mtd_cost(daily_report, today)
    today_v = today_cost(daily_report, today)
    burn = active_block_burn(blocks_report) if blocks_report is not None else None
    cache_ratio = aggregate_cache_hit_ratio(daily_report)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MTD cost", f"${mtd:,.2f}")
    c2.metric("Today", f"${today_v:,.2f}")
    c3.metric(
        "Active block $/hr",
        f"${burn:,.2f}" if burn is not None else "—",
        help="Cost-per-hour for the currently-active 5-hour billing window.",
    )
    c4.metric(
        "Cache hit ratio",
        f"{cache_ratio:.1%}",
        help="cache_read / (input + cache_create + cache_read), aggregated.",
    )


def _handle_day_click(event, nav: Navigation) -> None:
    """If the user clicked a point in an overview chart, drill into that day."""
    if not event:
        return
    selection = getattr(event, "selection", None)
    if not selection:
        return
    points = getattr(selection, "points", None) or []
    if not points:
        return
    raw = points[0].get("x")
    if not raw:
        return
    day = str(raw)[:10]  # YYYY-MM-DD prefix
    target = nav.to_day(day)
    st.query_params.clear()
    for k, v in target.to_params().items():
        st.query_params[k] = v
    st.rerun()
