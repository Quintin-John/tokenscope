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

from tokenscope import config, data
from tokenscope.analytics import (
    available_models,
    friendly_project_label,
    short_model_label,
)
from tokenscope.ccusage import CcusageError
from tokenscope.plans import Plan, get_plan, plan_names
from tokenscope.query import Query
from tokenscope.tz import detect_local_iana


DEFAULT_RANGE_DAYS = config.DEFAULT_RANGE_DAYS
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


def _seed_session_from_url() -> None:
    """Slice 15: shareable URLs.

    If a widget's `session_state` key isn't set yet (first render of the
    session) and a corresponding query-param is present in the URL, seed
    session_state so the widget renders with the URL's value. After the
    initial seed, session_state is the source of truth and the URL is
    written back by `_sync_url_from_session`.
    """
    params = st.query_params
    if _KEY_DATE_RANGE not in st.session_state:
        since_raw, until_raw = params.get("since"), params.get("until")
        if since_raw and until_raw:
            try:
                st.session_state[_KEY_DATE_RANGE] = (
                    date.fromisoformat(since_raw),
                    date.fromisoformat(until_raw),
                )
            except ValueError:
                pass
    if _KEY_OFFLINE not in st.session_state and "offline" in params:
        st.session_state[_KEY_OFFLINE] = params["offline"] == "true"
    if _KEY_PROJECT not in st.session_state and "project" in params:
        st.session_state[_KEY_PROJECT] = params["project"]
    if _KEY_MODELS not in st.session_state and "models" in params:
        raw = params["models"]
        st.session_state[_KEY_MODELS] = [m for m in raw.split(",") if m]
    if _KEY_PLAN not in st.session_state and "plan" in params:
        if params["plan"] in plan_names():
            st.session_state[_KEY_PLAN] = params["plan"]


def _sync_url_from_session(
    since_date: date,
    until_date: date,
    offline: bool,
    project_value: str | None,
    selected_models: list[str],
    plan_name: str,
) -> None:
    """Slice 15: write the current sidebar state back into the URL so the
    page is bookmarkable / shareable.

    Only writes when the desired value differs from what's already there,
    so we don't churn the URL on every rerun. Defaults are omitted
    (Enterprise plan, offline=False, all-models) — keeps shared links short.
    """
    desired: dict[str, str | None] = {
        "since": since_date.isoformat() if since_date else None,
        "until": until_date.isoformat() if until_date else None,
        "offline": "true" if offline else None,
        "project": project_value,
        "models": ",".join(selected_models) if selected_models else None,
        "plan": plan_name if plan_name != "Enterprise" else None,
    }
    for key, value in desired.items():
        cur = st.query_params.get(key)
        if value is None:
            if cur is not None:
                del st.query_params[key]
        elif cur != value:
            st.query_params[key] = value


def render(today: date | None = None) -> SidebarState:
    today = today or date.today()
    default_start = today - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    local_tz = detect_local_iana()
    _seed_session_from_url()

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
            tz=local_tz,
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
            # Drop the URL params too so a shared link doesn't reload the
            # same state the user just cleared.
            for url_key in ("since", "until", "offline", "project", "models", "plan"):
                if url_key in st.query_params:
                    del st.query_params[url_key]
            st.rerun()

        st.caption(
            f"Times in `{local_tz}` (auto-detected). Override with the `TZ` "
            "env var if it's wrong."
        )

    # Mirror the sidebar state into the URL so the page is shareable.
    _sync_url_from_session(
        since_date=since_date,
        until_date=until_date,
        offline=offline,
        project_value=project_value,
        selected_models=list(selected_models),
        plan_name=plan_name,
    )

    return SidebarState(
        query=Query(
            since=_to_ccusage_date(since_date),
            until=_to_ccusage_date(until_date),
            project=project_value,
            offline=offline,
            tz=local_tz,
        ),
        plan=get_plan(plan_name),
        selected_models=tuple(selected_models),
    )


__all__ = ["SidebarState", "render", "ALL_PROJECTS"]
