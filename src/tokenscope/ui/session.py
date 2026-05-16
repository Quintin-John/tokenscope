"""Session-detail view: KPIs, cost-by-model donut, token-mix bar."""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import find_session
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.ui import breadcrumbs
from tokenscope.ui.charts import donut_cost_by_model, session_token_mix
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    if not nav.session:
        st.warning("No session selected.")
        return

    breadcrumbs.render(nav)
    st.subheader("Session detail")
    st.caption(f"`{nav.session}`")

    try:
        report = data.session(state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    entry = find_session(report, nav.session)
    if entry is None:
        st.caption("Session not found in the selected date range.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cost", f"${entry.total_cost:,.2f}")
    c2.metric("Total tokens", f"{entry.total_tokens:,}")
    c3.metric("Models used", str(len(entry.models_used)))
    c4.metric("Last activity", entry.last_activity)

    st.caption(f"Project: `{entry.project_path}`")
    st.divider()

    left, right = st.columns([1, 1])
    with left:
        st.markdown("**Cost share by model**")
        fig = donut_cost_by_model(entry)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No model breakdown.")
    with right:
        st.markdown("**Token mix**")
        st.plotly_chart(session_token_mix(entry), width="stretch")
