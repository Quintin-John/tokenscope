"""Sidebar controls for the overview view (Phase 3).

Three controls today: date range, offline toggle, plan selector. Phase 4
will add model + project filters and (per PLAN §3.3) mirror state into
`st.query_params`. The sidebar returns an immutable `SidebarState` so the
main pane can be a pure consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import streamlit as st

from tokenscope.plans import PLANS, Plan, get_plan, plan_names
from tokenscope.query import Query


DEFAULT_RANGE_DAYS = 30


@dataclass(frozen=True, slots=True)
class SidebarState:
    query: Query
    plan: Plan


def _to_ccusage_date(d: date | None) -> str | None:
    return d.strftime("%Y%m%d") if d is not None else None


def render(today: date | None = None) -> SidebarState:
    """Render the sidebar and return the resulting state.

    `today` defaults to `date.today()`; injectable for tests / reproducible
    screenshots.
    """
    today = today or date.today()
    default_start = today - timedelta(days=DEFAULT_RANGE_DAYS - 1)

    with st.sidebar:
        st.markdown("### Filters")
        range_value = st.date_input(
            "Date range",
            value=(default_start, today),
            max_value=today,
            help="Trims the ccusage report via --since / --until (YYYYMMDD).",
        )
        if isinstance(range_value, tuple) and len(range_value) == 2:
            since_date, until_date = range_value
        else:
            # st.date_input returns a single date until the user picks the second one.
            single = range_value if isinstance(range_value, date) else default_start
            since_date, until_date = single, single

        offline = st.toggle(
            "Offline pricing",
            value=False,
            help="Pass --offline to ccusage so pricing comes from its cached data.",
        )

        st.markdown("### Plan")
        plan_name = st.selectbox(
            "Subscription",
            options=plan_names(),
            index=0,  # Enterprise default
            help="Pure labelling — does not change any cost numbers.",
        )

    return SidebarState(
        query=Query(
            since=_to_ccusage_date(since_date),
            until=_to_ccusage_date(until_date),
            offline=offline,
        ),
        plan=get_plan(plan_name),
    )


__all__ = ["SidebarState", "render", "PLANS"]
