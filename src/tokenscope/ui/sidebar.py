"""Sidebar controls.

Phase 4 additions over Phase 3:
- Project selectbox, populated from `daily_by_project`'s keys.
- Model multi-select, populated from `models_used` across the date range.

Both filter dropdowns are populated from a cached discovery query that
ignores the model/project filters themselves — so the option lists stay
stable as the user toggles filters within the same date range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import streamlit as st

from tokenscope import data
from tokenscope.analytics import available_models
from tokenscope.ccusage import CcusageError
from tokenscope.plans import Plan, get_plan, plan_names
from tokenscope.query import Query


DEFAULT_RANGE_DAYS = 30
ALL_PROJECTS = "All projects"


@dataclass(frozen=True, slots=True)
class SidebarState:
    query: Query
    plan: Plan
    selected_models: tuple[str, ...]


def _to_ccusage_date(d: date | None) -> str | None:
    return d.strftime("%Y%m%d") if d is not None else None


def render(today: date | None = None) -> SidebarState:
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
            single = range_value if isinstance(range_value, date) else default_start
            since_date, until_date = single, single

        offline = st.toggle(
            "Offline pricing",
            value=False,
            help="Pass --offline to ccusage so pricing comes from its cached data.",
        )

        # Discovery query: same date range + offline, no model/project filter.
        # Cached via @st.cache_data, so reopening the page within 30s is free.
        discovery_query = Query(
            since=_to_ccusage_date(since_date),
            until=_to_ccusage_date(until_date),
            offline=offline,
        )

        model_options: list[str] = []
        project_options: list[str] = []
        try:
            discovery_daily = data.daily(discovery_query)
            model_options = available_models(discovery_daily)
        except CcusageError:
            pass
        try:
            discovery_proj = data.daily_by_project(discovery_query)
            project_options = sorted(discovery_proj.projects.keys())
        except CcusageError:
            pass

        project_choice = st.selectbox(
            "Project",
            options=[ALL_PROJECTS, *project_options],
            index=0,
            help="Filters via ccusage's -p flag. Choose 'All projects' to disable.",
        )
        project_value: str | None = (
            None if project_choice == ALL_PROJECTS else project_choice
        )

        selected_models = st.multiselect(
            "Models",
            options=model_options,
            default=model_options,
            help="Post-fetch filter on the model breakdowns within each entry.",
        )

        st.markdown("### Plan")
        plan_name = st.selectbox(
            "Subscription",
            options=plan_names(),
            index=0,
            help="Pure labelling — does not change any cost numbers.",
        )

    return SidebarState(
        query=Query(
            since=_to_ccusage_date(since_date),
            until=_to_ccusage_date(until_date),
            project=project_value,
            offline=offline,
        ),
        plan=get_plan(plan_name),
        selected_models=tuple(selected_models),
    )


__all__ = ["SidebarState", "render", "ALL_PROJECTS"]
