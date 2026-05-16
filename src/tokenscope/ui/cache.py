"""Cache view: hit-ratio over time + estimated $ saved by cache reads."""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    aggregate_cache_hit_ratio,
    available_models,
    filter_daily_by_models,
)
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.ui.charts import cache_hit_ratio_line, dollars_saved_bar
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    if (banner := state.plan.banner_text()) is not None:
        st.info(banner)

    try:
        daily_report = data.daily(state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    all_models = available_models(daily_report)
    chosen = set(state.selected_models)
    if chosen and chosen != set(all_models):
        daily_report = filter_daily_by_models(daily_report, chosen)

    aggregate = aggregate_cache_hit_ratio(daily_report)
    st.metric("Cache hit ratio (window)", f"{aggregate:.1%}")

    if not daily_report.daily:
        st.caption("No usage in the selected window.")
        return

    st.subheader("Cache hit ratio over time")
    fig = cache_hit_ratio_line(daily_report)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")

    st.subheader("Estimated $ saved by cache reads")
    st.caption(
        "Cache-read tokens valued at the uncached input rate "
        "(`tokenscope.pricing.INPUT_PRICE_USD_PER_MTOK_BY_FAMILY`)."
    )
    fig = dollars_saved_bar(daily_report)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")
