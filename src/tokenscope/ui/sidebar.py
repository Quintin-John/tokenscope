"""Sidebar controls.

Slice 9 additions over slices 3–4:
- Friendly labels in the Project and Models widgets via `format_func` so
  slugified paths and date-suffixed model names are scannable. Raw values
  still flow into Query and ccusage unchanged.
- Reset filters button at the bottom that wipes the relevant session_state
  keys and reruns. Clears date range / project / models / offline / plan
  back to defaults.

Every widget has a stable `key` so the reset works deterministically.

(Slice 9 originally also added preset-buttons above the date_input; that
was reverted on user feedback — the existing date_input alone is enough
and the row of buttons added visual clutter without earning its space.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    available_models,
    friendly_project_label,
    short_model_label,
)
from tokenscope.ccusage import CcusageError
from tokenscope.plans import Plan, get_plan, plan_names
from tokenscope.query import Query


DEFAULT_RANGE_DAYS = 30
ALL_PROJECTS = "All projects"


def _home_slug() -> str:
    """Slugify the user's home directory the way ccusage encodes paths.

    `/Users/quintin-johnsmith` → `-Users-quintin-johnsmith`. We pass this
    into `friendly_project_label` so it can substitute the prefix with
    `~`. Done at render time (not import time) so test runs with custom
    `HOME` environments behave correctly.
    """
    return "-" + str(Path.home()).lstrip("/").replace("/", "-")

# Widget keys — used by the Reset button to clear state deterministically.
_KEY_DATE_RANGE = "sidebar-date-range"
_KEY_OFFLINE = "sidebar-offline"
_KEY_PROJECT = "sidebar-project"
_KEY_MODELS = "sidebar-models"
_KEY_PLAN = "sidebar-plan"


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

        # date_input reads from session_state when the key is set; falls back
        # to the 30d default on first render.
        date_kwargs: dict = {"key": _KEY_DATE_RANGE, "max_value": today}
        if _KEY_DATE_RANGE not in st.session_state:
            date_kwargs["value"] = (default_start, today)
        range_value = st.date_input(
            "Date range",
            help="Trims the ccusage report via --since / --until (YYYYMMDD).",
            **date_kwargs,
        )
        if isinstance(range_value, tuple) and len(range_value) == 2:
            since_date, until_date = range_value
        else:
            single = range_value if isinstance(range_value, date) else default_start
            since_date, until_date = single, single

        offline_kwargs: dict = {"key": _KEY_OFFLINE}
        if _KEY_OFFLINE not in st.session_state:
            offline_kwargs["value"] = False
        offline = st.toggle(
            "Offline pricing",
            help="Pass --offline to ccusage so pricing comes from its cached data.",
            **offline_kwargs,
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

        project_kwargs: dict = {"key": _KEY_PROJECT}
        if _KEY_PROJECT not in st.session_state:
            project_kwargs["index"] = 0
        home = _home_slug()
        project_choice = st.selectbox(
            "Project",
            options=[ALL_PROJECTS, *project_options],
            help="Filters via ccusage's -p flag. Choose 'All projects' to disable.",
            format_func=lambda v: (
                v if v == ALL_PROJECTS else friendly_project_label(v, home_slug=home)
            ),
            **project_kwargs,
        )
        project_value: str | None = (
            None if project_choice == ALL_PROJECTS else project_choice
        )

        models_kwargs: dict = {"key": _KEY_MODELS}
        if _KEY_MODELS not in st.session_state:
            models_kwargs["default"] = model_options
        selected_models = st.multiselect(
            "Models",
            options=model_options,
            help="Post-fetch filter on the model breakdowns within each entry.",
            format_func=short_model_label,
            **models_kwargs,
        )

        st.markdown("### Plan")
        plan_kwargs: dict = {"key": _KEY_PLAN}
        if _KEY_PLAN not in st.session_state:
            plan_kwargs["index"] = 0
        plan_name = st.selectbox(
            "Subscription",
            options=plan_names(),
            help="Pure labelling — does not change any cost numbers.",
            **plan_kwargs,
        )

        st.markdown("")
        if st.button(
            "Reset filters",
            key="sidebar-reset",
            help="Clears date range, project, models, offline, and plan back "
                 "to defaults (last 30 days, all projects, all models, online, "
                 "Enterprise).",
            width="stretch",
        ):
            for k in (
                _KEY_DATE_RANGE,
                _KEY_OFFLINE,
                _KEY_PROJECT,
                _KEY_MODELS,
                _KEY_PLAN,
            ):
                st.session_state.pop(k, None)
            st.rerun()

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
