"""Sidebar controls.

Renders the date range / pricing toggle / project / models / plan
filter set plus a Reset action. Per PLAN.md and README.md these are
all deliberate: the plan selector re-frames the cost KPIs (Enterprise
vs flat-rate), the offline toggle drives ccusage's --offline flag, the
timezone caption is the user-visible signal that auto-detection
worked (a wrong TZ silently mis-buckets days).

The visual layer (scoped CSS) lives in `_sidebar_styles.css` next to
this module — pure CSS in a .css file, not interpolated into Python
strings. The sidebar reads it once at import time and injects it via
`st.markdown(..., unsafe_allow_html=True)` on every render.

Help text strings are module-level constants so the widgets don't
carry inline copy at their call sites.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from tokenscope import config, data
from tokenscope.analytics import (
    available_models,
    short_model_label,
)
from tokenscope.ccusage import CcusageError
from tokenscope.log import get_logger
from tokenscope.paths import project_display_name
from tokenscope.plans import DEFAULT_PLAN, Plan, get_plan, plan_names
from tokenscope.query import Query
from tokenscope.tz import detect_local_iana

_log = get_logger(__name__)


DEFAULT_RANGE_DAYS = config.DEFAULT_RANGE_DAYS
ALL_PROJECTS = "All projects"

# CSS lives in a sibling file so colors / spacing / selector overrides
# are maintainable as CSS, not as interpolated strings. Read once at
# module load; injected on every sidebar render.
_SIDEBAR_CSS = (Path(__file__).parent / "_sidebar_styles.css").read_text()

# Help-text constants. Kept here (not at the widget call site) so the
# product language can be tuned in one place and the widget call stays
# readable. Only widgets whose label genuinely needs an explanation get
# a `?` icon — Date range / Project / Models are self-evident.
_HELP_OFFLINE_PRICING = (
    "Use ccusage's locally-cached pricing data instead of fetching "
    "fresh rates from LiteLLM. Useful when offline or rate-limited; "
    "may diverge from current rates if the cache is stale."
)
_HELP_PLAN = (
    "Enterprise: pay-per-token API billing. The Window cost KPI is "
    "your actual bill.\n\n"
    "Pro / Max 5× / Max 20×: flat-rate subscription. The KPI flips "
    "to the prorated plan fee as the headline, with what the same "
    "usage would have cost at API rates as the delta — useful for "
    "seeing the savings vs pay-per-token."
)


# Widget keys — used by the Reset button to clear state deterministically.
_KEY_DATE_RANGE = "sidebar-date-range"
_KEY_DATE_PRESET = "sidebar-date-preset"
_KEY_OFFLINE = "sidebar-offline"
_KEY_PROJECT = "sidebar-project"
_KEY_MODELS = "sidebar-models"
_KEY_PLAN = "sidebar-plan"

# Internal flag — Clear-all sets this on click; the Models renderer
# applies it on the *next* pass, BEFORE the multiselect instantiates.
# Streamlit forbids direct assignment to a widget-keyed session_state
# slot after the widget has been instantiated, so the only safe place
# to seed `_KEY_MODELS = []` is the top of the next render.
_KEY_MODELS_CLEAR_PENDING = "sidebar-models-clear-pending"


@dataclass(frozen=True, slots=True)
class SidebarState:
    query: Query
    plan: Plan
    selected_models: tuple[str, ...]


def _to_ccusage_date(d: date | None) -> str | None:
    return d.strftime("%Y%m%d") if d is not None else None


def _inject_sidebar_styles() -> None:
    """Inject the sidebar CSS overrides. Called once per render — cheap;
    Streamlit deduplicates identical style blocks across reruns."""
    st.markdown(f"<style>{_SIDEBAR_CSS}</style>", unsafe_allow_html=True)


# --- date-range presets ---------------------------------------------------


def _last_n_days_range(today: date, n: int) -> tuple[date, date]:
    """Inclusive "last N days" range. `n=7` → `(today - 6, today)`,
    which is 7 days inclusive of today — the conventional reading of
    `7d` in dashboards."""
    return today - timedelta(days=n - 1), today


def _month_to_date_range(today: date) -> tuple[date, date]:
    """First day of the current month → today."""
    return today.replace(day=1), today


def _custom_range_marker(_today: date) -> None:
    """Sentinel builder for the Custom preset — leaves the date range
    untouched so the underlying date_input controls the value."""
    return None


@dataclass(frozen=True, slots=True)
class _DatePreset:
    """One row of the date-preset registry.

    `builder` returns `(since, until)` for an active preset or `None`
    for `Custom` (the passive "I'll pick manually" choice). Treating
    Custom as a regular preset with a no-op builder keeps the
    segmented-control rendering DRY — no special-case at the call site.
    """
    label: str
    builder: Callable[[date], tuple[date, date] | None]


# Single source of truth for the preset chip row. The segmented-control
# options are derived from `.label`; the on-click dispatch consults
# `.builder`. Adding a "60d" preset is one new entry.
_DATE_PRESETS: tuple[_DatePreset, ...] = (
    _DatePreset("7d", lambda today: _last_n_days_range(today, 7)),
    _DatePreset("30d", lambda today: _last_n_days_range(today, 30)),
    _DatePreset("MTD", _month_to_date_range),
    _DatePreset("Custom", _custom_range_marker),
)


def _resolve_date_preset(
    label: str | None, today: date
) -> tuple[date, date] | None:
    """Pure resolver: preset label → date range.

    Returns `None` when:
      - the label is unset (segmented control returns None before
        any click),
      - the label is unknown (defensive — shouldn't happen via the
        widget, but a forged URL or stale session_state value could
        leak one in),
      - the preset is passive (Custom builder returns None).

    Pulling the dispatch out of the `on_change` handler keeps the
    handler a thin shim over Streamlit's session_state I/O and lets
    the resolution itself be unit-tested without mocking the
    Streamlit runtime.
    """
    if not label:
        return None
    preset = next((p for p in _DATE_PRESETS if p.label == label), None)
    if preset is None:
        return None
    return preset.builder(today)


def _apply_date_preset_change() -> None:
    """`on_change` handler for the date-preset segmented control.

    Reads the new selection from session_state, resolves it via the
    pure helper, and (for active presets) seeds `_KEY_DATE_RANGE` so
    the date_input picks the new value up on the next pass.
    """
    label = st.session_state.get(_KEY_DATE_PRESET)
    range_value = _resolve_date_preset(label, date.today())
    if range_value is not None:
        st.session_state[_KEY_DATE_RANGE] = range_value
        _log.info("sidebar.date_preset_applied preset=%s", label)


def _render_date_range_presets() -> None:
    """Preset chips above the date input. The segmented control's
    active state inherits `[theme] primaryColor` — the brand accent."""
    st.segmented_control(
        "Date preset",
        options=[p.label for p in _DATE_PRESETS],
        key=_KEY_DATE_PRESET,
        on_change=_apply_date_preset_change,
        label_visibility="collapsed",
    )


# --- URL <-> session_state sync ------------------------------------------


def _seed_session_from_url() -> None:
    """Shareable URLs: on first render of the session, seed any widget
    state that's encoded in the URL. After the initial seed,
    session_state is the source of truth and `_sync_url_from_session`
    writes back."""
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
    # Models has an unconditional URL → session_state sync (not the
    # "seed only on first render" pattern used by every other widget
    # here). Reason: the Models view's "View [family] in Overview →"
    # drill button writes `?models=<family-ids>` to `st.query_params`
    # and reruns. By that point the sidebar's session_state already
    # holds the PRIOR selection from this session — without an
    # unconditional sync, the seeded URL value would be ignored and
    # the drill would silently fail (sidebar widget still shows the
    # old selection).
    #
    # Writing to `_KEY_MODELS` here is safe because this function
    # runs at the TOP of the sidebar render — strictly before the
    # multiselect widget is instantiated. The "no assignment after
    # widget instantiation" Streamlit guard doesn't trip.
    #
    # The previous attempt (set `st.session_state["sidebar-models"]
    # = fam_models` from inside the Models view, AFTER the sidebar
    # had already rendered) is exactly what crashes — handled here
    # at the only point in the script run where the assignment is
    # legal.
    if "models" in params:
        raw = params["models"]
        new_value = [m for m in raw.split(",") if m]
        if st.session_state.get(_KEY_MODELS) != new_value:
            st.session_state[_KEY_MODELS] = new_value
    if _KEY_PLAN not in st.session_state and "plan" in params:
        if params["plan"] in plan_names():
            st.session_state[_KEY_PLAN] = params["plan"]


def _plan_url_value(plan_name: str) -> str | None:
    """The `plan` URL value for `plan_name`: ``None`` for the default
    plan (omitted to keep shared links short), otherwise the name.

    Keys off `plans.DEFAULT_PLAN` rather than a hardcoded name so the
    default and the omission rule stay in sync — reordering PLANS or
    renaming the default updates one place.
    """
    return plan_name if plan_name != DEFAULT_PLAN.name else None


def _sync_url_from_session(
    since_date: date,
    until_date: date,
    offline: bool,
    project_value: str | None,
    selected_models: list[str],
    plan_name: str,
) -> None:
    """Write current sidebar state back into the URL so the page is
    bookmarkable / shareable. Defaults are omitted (default plan,
    offline=False, no model narrowing) — keeps shared links short."""
    desired: dict[str, str | None] = {
        "since": since_date.isoformat() if since_date else None,
        "until": until_date.isoformat() if until_date else None,
        "offline": "true" if offline else None,
        "project": project_value,
        "models": ",".join(selected_models) if selected_models else None,
        "plan": _plan_url_value(plan_name),
    }
    for key, value in desired.items():
        cur = st.query_params.get(key)
        if value is None:
            if cur is not None:
                del st.query_params[key]
        elif cur != value:
            st.query_params[key] = value


# --- widget renderers ----------------------------------------------------


def _render_date_range(today: date, default_start: date) -> tuple[date, date]:
    """Date-range widget. Returns (since, until) — when the user has
    selected a single day, both values are that day. No `help=` icon:
    a date range picker is self-evident, and stacking five identical
    `?` icons in the panel was visual noise.
    """
    date_kwargs: dict = {"key": _KEY_DATE_RANGE, "max_value": today}
    if _KEY_DATE_RANGE not in st.session_state:
        date_kwargs["value"] = (default_start, today)
    range_value = st.date_input("Date range", **date_kwargs)
    if isinstance(range_value, tuple) and len(range_value) == 2:
        return range_value
    single = range_value if isinstance(range_value, date) else default_start
    return single, single


def _render_offline_toggle() -> bool:
    """Offline-pricing toggle. Drives ccusage's `--offline` flag. The
    `?` icon stays — "Offline pricing" isn't self-explanatory in
    product language; the help copy unpacks what the toggle does."""
    offline_kwargs: dict = {"key": _KEY_OFFLINE}
    if _KEY_OFFLINE not in st.session_state:
        offline_kwargs["value"] = False
    return st.toggle(
        "Offline pricing", help=_HELP_OFFLINE_PRICING, **offline_kwargs
    )


def _fetch_discovery_options(query: Query) -> tuple[list[str], list[str]]:
    """Populate the Project / Models dropdown option lists.

    Two ccusage calls (daily for models, daily_by_project for projects)
    cached via `data.*`. Failures are swallowed so a first-run user
    without ccusage configured still gets a usable sidebar — the
    dropdowns just render empty option lists, the rest of the dashboard
    surfaces the underlying error via its own `st.error` paths.
    """
    model_options: list[str] = []
    project_options: list[str] = []
    try:
        discovery_daily = data.daily(query)
        model_options = available_models(discovery_daily)
    except CcusageError as exc:
        _log.warning("sidebar.discovery.daily_failed exc=%s", exc)
    try:
        discovery_proj = data.daily_by_project(query)
        project_options = sorted(discovery_proj.projects.keys())
    except CcusageError as exc:
        _log.warning("sidebar.discovery.by_project_failed exc=%s", exc)
    return model_options, project_options


def _render_project_selectbox(project_options: list[str]) -> str | None:
    """Project filter. Returns None for "All projects" so the caller
    can plumb it straight into `Query.project`. Display labels go
    through `project_display_name` — same helper the Daily table's
    Project column uses, one rule across the dashboard."""
    project_kwargs: dict = {"key": _KEY_PROJECT}
    if _KEY_PROJECT not in st.session_state:
        project_kwargs["index"] = 0
    project_choice = st.selectbox(
        "Project",
        options=[ALL_PROJECTS, *project_options],
        format_func=lambda v: (
            v if v == ALL_PROJECTS else project_display_name(v)
        ),
        **project_kwargs,
    )
    return None if project_choice == ALL_PROJECTS else project_choice


def _render_models_multiselect(model_options: list[str]) -> list[str]:
    """Models filter (post-fetch).

    Renders the multiselect plus a paired Select all / Clear all row.
    Both buttons are scoped escape hatches — they only touch this
    widget's session_state + URL param, never any other filter.

    On first render every available model is selected by default so
    the dashboard shows everything until the user narrows in.
    """
    # Apply a deferred Clear-all from the prior render BEFORE the
    # multiselect instantiates — Streamlit forbids assignment to a
    # widget-keyed session_state slot after the widget renders.
    if st.session_state.pop(_KEY_MODELS_CLEAR_PENDING, False):
        st.session_state[_KEY_MODELS] = []

    models_kwargs: dict = {"key": _KEY_MODELS}
    if _KEY_MODELS not in st.session_state:
        models_kwargs["default"] = model_options
    selected = list(
        st.multiselect(
            "Models",
            options=model_options,
            format_func=short_model_label,
            **models_kwargs,
        )
    )

    btn_cols = st.columns(2)
    if btn_cols[0].button(
        "Select all",
        key="sidebar-models-select-all",
        width="stretch",
    ):
        _log.info("sidebar.models_select_all_clicked")
        st.session_state.pop(_KEY_MODELS, None)
        if "models" in st.query_params:
            del st.query_params["models"]
        st.rerun()
    if btn_cols[1].button(
        "Clear all",
        key="sidebar-models-clear-all",
        width="stretch",
    ):
        _log.info("sidebar.models_clear_all_clicked")
        st.session_state[_KEY_MODELS_CLEAR_PENDING] = True
        if "models" in st.query_params:
            del st.query_params["models"]
        st.rerun()
    return selected


def _render_plan_selectbox() -> str:
    """Subscription-plan selector. Re-frames the Window cost KPI from
    pay-per-token (Enterprise) to flat-rate (Pro / Max). The `?` icon
    stays — the headline-flipping behaviour isn't obvious from the
    label alone."""
    plan_kwargs: dict = {"key": _KEY_PLAN}
    if _KEY_PLAN not in st.session_state:
        plan_kwargs["index"] = plan_names().index(DEFAULT_PLAN.name)
    return st.selectbox(
        "Subscription", options=plan_names(), help=_HELP_PLAN, **plan_kwargs
    )


def _render_reset_button() -> None:
    """Reset-filters button. Clears every sidebar widget key from
    session_state and drops the corresponding URL params, then reruns —
    so a shared link doesn't reload the state the user just cleared."""
    if not st.button(
        "Reset filters", key="sidebar-reset", width="stretch"
    ):
        return
    _log.info("sidebar.reset_clicked")
    for k in (
        _KEY_DATE_RANGE,
        _KEY_DATE_PRESET,
        _KEY_OFFLINE,
        _KEY_PROJECT,
        _KEY_MODELS,
        _KEY_PLAN,
        _KEY_MODELS_CLEAR_PENDING,
    ):
        st.session_state.pop(k, None)
    for url_key in ("since", "until", "offline", "project", "models", "plan"):
        if url_key in st.query_params:
            del st.query_params[url_key]
    st.rerun()


def _render_timezone_caption(local_tz: str) -> None:
    """Detected-timezone line. Plain prose — no inline-code backticks,
    no CLI env-var instructions. The README documents the `TZ` override
    for users who need it; the sidebar isn't the place for that.
    """
    st.caption(f"Times shown in {local_tz}.")


# --- composition ---------------------------------------------------------


def render(today: date | None = None) -> SidebarState:
    """Render the sidebar and return the SidebarState that drives every
    downstream view. Composes the section renderers in fixed order;
    each renderer owns exactly one widget plus its session_state /
    URL-param wiring."""
    today = today or date.today()
    default_start = today - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    local_tz = detect_local_iana()
    _seed_session_from_url()

    with st.sidebar:
        _inject_sidebar_styles()
        st.markdown("### Filters")
        _render_date_range_presets()
        since_date, until_date = _render_date_range(today, default_start)
        offline = _render_offline_toggle()

        # Discovery query: same date range + offline, no model/project
        # filter. Cached via @st.cache_data, so reopening the page within
        # 30s is free.
        discovery_query = Query(
            since=_to_ccusage_date(since_date),
            until=_to_ccusage_date(until_date),
            offline=offline,
            tz=local_tz,
        )
        model_options, project_options = _fetch_discovery_options(discovery_query)

        project_value = _render_project_selectbox(project_options)
        selected_models = _render_models_multiselect(model_options)

        st.markdown("### Plan")
        plan_name = _render_plan_selectbox()

        _render_reset_button()
        _render_timezone_caption(local_tz)

    _sync_url_from_session(
        since_date=since_date,
        until_date=until_date,
        offline=offline,
        project_value=project_value,
        selected_models=selected_models,
        plan_name=plan_name,
    )

    _log.debug(
        "sidebar.state since=%s until=%s project=%s offline=%s models=%s plan=%s",
        since_date,
        until_date,
        project_value,
        offline,
        selected_models,
        plan_name,
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
