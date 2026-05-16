"""Overview page: KPIs + three charts. Phase 3 deliverable, no drill-down.

Reads `SidebarState` (date range + offline + plan), calls the cached data
layer, renders KPIs + charts. If ccusage returns no entries for the
selected window, falls back to an empty state instead of breaking the
chart builders.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    active_block_burn,
    aggregate_cache_hit_ratio,
    mtd_cost,
    today_cost,
)
from tokenscope.ccusage import CcusageError
from tokenscope.ui.charts import (
    rolling_average_line,
    stacked_area_cost_by_family,
    token_mix_bar,
)
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, today: date | None = None) -> None:
    today = today or date.today()

    if (banner := state.plan.banner_text()) is not None:
        st.info(banner)

    try:
        daily_report = data.daily(state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    try:
        blocks_report = data.blocks(active=True, query=state.query)
    except CcusageError:
        # Blocks --active sometimes returns nothing; degrade gracefully on the
        # KPI rather than failing the whole page.
        blocks_report = None

    _render_kpis(daily_report, blocks_report, today)
    st.divider()

    if not daily_report.daily:
        st.caption("No usage in the selected window.")
        return

    st.subheader("Daily cost by model family")
    fig = stacked_area_cost_by_family(daily_report)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")

    st.subheader("7-day rolling average cost")
    fig = rolling_average_line(daily_report, window_days=7)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")

    st.subheader("Daily token mix")
    fig = token_mix_bar(daily_report)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")


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
