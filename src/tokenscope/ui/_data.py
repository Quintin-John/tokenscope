"""Shared data-loading helpers — DRY pass over per-view duplication.

`overview.py`, `cache.py`, and `models.py` each carried the same
~10-line prelude: fetch `data.daily(query)`, handle CcusageError by
rendering `st.error`, then apply the sidebar's model multi-select via
`filter_daily_by_models` when it narrows below "all".

Consolidating here:

  * `load_daily(state)` — fetch + filter + error-handling in one call.
    Returns the filtered `DailyReport` or `None` (caller should
    short-circuit on None — `st.error` is already rendered).

Tests live where the callers do; this is glue.
"""

from __future__ import annotations

import streamlit as st

from tokenscope import data
from tokenscope.analytics import available_models, filter_daily_by_models
from tokenscope.ccusage import CcusageError
from tokenscope.log import get_logger
from tokenscope.models import DailyReport
from tokenscope.ui.sidebar import SidebarState

_log = get_logger(__name__)


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
        _log.error("data.load.daily_failed exc=%s", exc)
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return None

    all_models = available_models(report)
    chosen = set(state.selected_models)
    if chosen and chosen != set(all_models):
        report = filter_daily_by_models(report, chosen)
    return report
