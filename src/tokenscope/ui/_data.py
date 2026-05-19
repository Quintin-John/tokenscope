"""Shared data-loading helpers — DRY pass over per-view duplication.

`overview.py`, `cache.py`, and `models.py` each carried the same
~10-line prelude: fetch `data.daily(query)`, handle CcusageError by
rendering `st.error`, then apply the sidebar's model multi-select via
`filter_daily_by_models` when it narrows below "all".

Consolidating here:

  * `load_daily(state)` / `load_daily_by_project(state)` — fetch +
    filter + error-handling in one call. Returns the filtered report
    or `None` (caller should short-circuit on None — `st.error` is
    already rendered).

Path helpers (`home_slug`) live in `tokenscope.paths` to avoid a
circular import with `ui.sidebar` (which defines `SidebarState`
consumed by the loaders below).

Tests live where the callers do; this is glue.
"""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    available_models,
    available_models_by_project,
    filter_daily_by_models,
    filter_daily_by_project_models,
)
from tokenscope.ccusage import CcusageError
from tokenscope.log import get_logger
from tokenscope.models import DailyByProjectReport, DailyReport
from tokenscope.ui.sidebar import SidebarState

_log = get_logger(__name__)


def _render_ccusage_error(label: str, exc: CcusageError) -> None:
    """Single error-rendering rule shared by every loader. Logs at ERROR
    with the loader label (so log greps stay specific) and renders
    Streamlit's red banner with the underlying exception text.

    Callers should `return None` after invoking; the loaders below do
    exactly that. Extracted so future loaders share the failure
    presentation without copy-pasting the format string."""
    _log.error("data.load.%s_failed exc=%s", label, exc)
    st.error(f"ccusage failed:\n\n```\n{exc}\n```")


def load_daily(state: SidebarState) -> DailyReport | None:
    """Fetch the daily report for the sidebar's current query, apply the
    model-multiselect post-fetch filter, return the result.

    Renders `st.error(...)` and returns None when ccusage fails — the
    caller should `return` on None to bail out of the rest of its
    render.
    """
    try:
        report = data.daily(state.query)
    except CcusageError as exc:
        _render_ccusage_error("daily", exc)
        return None

    all_models = available_models(report)
    chosen = set(state.selected_models)
    if chosen and chosen != set(all_models):
        report = filter_daily_by_models(report, chosen)
    return report


def load_daily_by_project(
    state: SidebarState,
) -> DailyByProjectReport | None:
    """Fetch the daily-by-project report for the sidebar's current query,
    apply the model-multiselect post-fetch filter, return the result.

    Sibling of `load_daily` — identical failure presentation and identical
    filter-passthrough semantics. The filter itself shares the
    `_filter_entries_by_models` core with `filter_daily_by_models` so the
    two data paths can't drift on what "filter by model X" means.
    """
    try:
        report = data.daily_by_project(state.query)
    except CcusageError as exc:
        _render_ccusage_error("daily_by_project", exc)
        return None

    all_models = available_models_by_project(report)
    chosen = set(state.selected_models)
    if chosen and chosen != set(all_models):
        report = filter_daily_by_project_models(report, chosen)
    return report
