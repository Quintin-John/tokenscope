"""Block-detail view: KPIs + burn-rate gauge + projection."""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import find_block
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.ui import breadcrumbs
from tokenscope.ui.charts import burn_gauge
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    breadcrumbs.render(nav)
    if not nav.block:
        st.warning("No block selected. Use the breadcrumb above to go back.")
        return

    st.subheader("Block detail")
    st.caption(f"`{nav.block}`")

    try:
        report = data.blocks(active=False, query=state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    block = find_block(report, nav.block)
    if block is None:
        st.caption("Block not found in the selected date range.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cost so far", f"${block.cost_usd:,.2f}")
    c2.metric("Total tokens", f"{block.total_tokens:,}")
    c3.metric("Active", "Yes" if block.is_active else "No")
    if block.burn_rate is not None:
        c4.metric("$/hr", f"${block.burn_rate.cost_per_hour:,.2f}")
    else:
        c4.metric("$/hr", "—")

    st.caption(
        f"Window {block.start_time} → {block.end_time}"
        + (f" (ended {block.actual_end_time})" if block.actual_end_time else "")
    )

    st.divider()
    gauge = burn_gauge(block)
    if gauge is not None:
        st.plotly_chart(gauge, width="stretch")
    else:
        st.caption("No burn rate available for this block.")

    if block.projection is not None:
        st.markdown("**Projection**")
        p = block.projection
        c1, c2, c3 = st.columns(3)
        c1.metric("Projected total cost", f"${p.total_cost:,.2f}")
        c2.metric("Projected total tokens", f"{p.total_tokens:,}")
        c3.metric("Minutes remaining", str(p.remaining_minutes))
