"""Models view: Sankey of token flow from kind → model family.

PLAN.md §3.2 calls for token-kind → model → cost. We collapse "model"
to family (opus / haiku / sonnet) for legend density and bake the
family's aggregate cost into its node label so the third "→ cost" hop
is conveyed without an additional misleading-units layer.
"""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import available_models, filter_daily_by_models
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.ui.charts import token_flow_sankey
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

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date range** "
            "in the sidebar, or clearing the **Project** filter if one is set."
        )
        return

    st.subheader("Token flow: kind → model family")
    st.caption(
        "Link widths are token counts. Family node labels carry the family's "
        "aggregate cost for the selected window."
    )
    fig = token_flow_sankey(daily_report)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")
    else:
        st.caption("No model breakdowns in the selected window.")
