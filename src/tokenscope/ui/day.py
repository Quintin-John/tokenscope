"""Day-detail view: KPIs, cost-by-model donut, sessions on day, blocks on day."""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    blocks_on_day,
    find_daily_entry,
    sessions_on_day,
)
from tokenscope.ccusage import CcusageError
from tokenscope.models import BlockEntry, SessionEntry
from tokenscope.navigation import Navigation
from tokenscope.ui import breadcrumbs
from tokenscope.ui._nav import route_to
from tokenscope.ui.charts import donut_cost_by_model
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    # Render the breadcrumb FIRST so even an incomplete URL
    # (`?view=day` with no `day` param) shows a back affordance.
    breadcrumbs.render(nav)
    if not nav.day:
        st.warning("No day selected. Click a day in the Overview charts or "
                   "use the breadcrumb above to go back.")
        return

    st.subheader(f"Day detail — {nav.day}")

    try:
        daily_report = data.daily(state.query)
        session_report = data.session(state.query)
        blocks_report = data.blocks(active=False, query=state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    entry = find_daily_entry(daily_report, nav.day)
    if entry is None:
        st.info(
            f"No usage recorded on `{nav.day}` in the current window. "
            "If you meant a different day, widen the **Date range** in the "
            "sidebar — the day must be inside the window for ccusage to "
            "include it. Use the breadcrumb above to go back to Overview."
        )
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Cost", f"${entry.total_cost:,.2f}")
    c2.metric("Total tokens", f"{entry.total_tokens:,}")
    c3.metric("Models used", str(len(entry.models_used)))

    st.divider()

    left, right = st.columns([1, 1])

    with left:
        st.markdown("**Cost share by model**")
        fig = donut_cost_by_model(entry)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No model breakdown for this day.")

    with right:
        st.markdown("**Sessions touched on this day**")
        sessions = sessions_on_day(session_report, nav.day)
        if not sessions:
            st.caption("No sessions whose last activity was on this day.")
        else:
            for session in sessions:
                _session_row(session, nav)

    st.markdown("**Blocks starting on this day** (UTC)")
    blocks = blocks_on_day(blocks_report, nav.day, tz=state.query.tz)
    if not blocks:
        st.caption("No blocks started on this day.")
    else:
        for block in blocks:
            _block_row(block, nav)


def _entity_row(
    *,
    id_label: str,
    cost: float,
    tokens: int,
    button_label: str,
    button_key: str,
    nav_target: Navigation,
) -> None:
    """One drill-down row in the day-detail view.

    `_session_row` and `_block_row` previously duplicated this exact
    layout (four columns, three markdowns, one secondary button) and
    differed only in the values and the nav target. The helper takes
    pre-formatted values so the caller owns entity-specific formatting
    (e.g. the block's "— **active**" suffix) without leaking entity
    types into here.

    `nav_target` is constructed eagerly by the caller — Navigation is a
    pure dataclass with no I/O cost, so deferring it via a callable
    would add complexity without saving any work.
    """
    cols = st.columns([5, 2, 2, 2])
    cols[0].markdown(id_label)
    cols[1].markdown(f"${cost:,.2f}")
    cols[2].markdown(f"{tokens:,} tok")
    if cols[3].button(button_label, key=button_key, type="secondary"):
        route_to(nav_target)


def _session_row(session: SessionEntry, nav: Navigation) -> None:
    _entity_row(
        id_label=f"`{session.session_id}`",
        cost=session.total_cost,
        tokens=session.total_tokens,
        button_label="Open session",
        button_key=f"open-session-{session.session_id}",
        nav_target=nav.to_session(session.session_id),
    )


def _block_row(block: BlockEntry, nav: Navigation) -> None:
    active_suffix = " — **active**" if block.is_active else ""
    _entity_row(
        id_label=f"`{block.id}`{active_suffix}",
        cost=block.cost_usd,
        tokens=block.total_tokens,
        button_label="Open block",
        button_key=f"open-block-{block.id}",
        nav_target=nav.to_block(block.id),
    )


