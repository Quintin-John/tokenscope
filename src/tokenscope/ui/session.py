"""Session-detail view: KPIs, cost-by-model donut, token-mix bar.

Slice 17 adds the blocks-on-this-day timeline — the last PLAN.md §3.1
drill ("Sessions on that day → Blocks within session") that was sitting
unbuilt since slice 4. ccusage doesn't link sessions and blocks by ID;
the timeline uses temporal proximity (blocks that started on the
session's lastActivity date) as the honest available heuristic. The
caption flags that approximation so the user isn't misled.
"""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import blocks_for_session, find_session
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.ui import breadcrumbs
from tokenscope.ui._nav import handle_chart_drill
from tokenscope.ui.charts import (
    donut_cost_by_model,
    session_blocks_timeline,
    session_token_mix,
)
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    breadcrumbs.render(nav)
    if not nav.session:
        st.warning("No session selected. Use the breadcrumb above to go back.")
        return

    st.subheader("Session detail")
    st.caption(f"`{nav.session}`")

    try:
        report = data.session(state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    # `nav.session_project` disambiguates when multiple sessions
    # share `nav.session` (the `subagents`-per-project case). When
    # absent (legacy shareable URL) and the lookup is ambiguous,
    # `find_session` returns None — failing closed rather than
    # silently picking the first match.
    entry = find_session(report, nav.session, nav.session_project)
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

    st.divider()
    _render_blocks_timeline(state, nav, entry)


def _render_blocks_timeline(state: SidebarState, nav: Navigation, entry) -> None:
    """Blocks-within-session timeline (PLAN.md §3.1 last drill)."""
    st.markdown("**Blocks on this day**")
    st.caption(
        "5-hour billing windows whose start time falls on this session's "
        f"last-activity date ({entry.last_activity}). ccusage doesn't link "
        "sessions to blocks directly, so this is a same-day proximity match — "
        "click a block to drill into its detail."
    )

    try:
        blocks_report = data.blocks(active=False, query=state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    blocks = blocks_for_session(blocks_report, entry, tz=state.query.tz)
    if not blocks:
        st.info(
            "No 5-hour billing blocks started on this day in the current "
            "window. Widen the **Date range** in the sidebar if you want "
            "the day's blocks to be visible."
        )
        return

    fig = session_blocks_timeline(blocks, tz=state.query.tz)
    if fig is None:
        return
    event = st.plotly_chart(
        fig,
        width="stretch",
        key="session-blocks-timeline",
        on_select="rerun",
        selection_mode=("points",),
    )
    handle_chart_drill(event, nav.to_block, chart_key="session-blocks-timeline")
