"""Shared navigation helpers — DRY pass over per-view duplication.

Before this module existed, six UI files each carried their own copy
of the same "clear query_params, write the target's params, rerun"
sequence (overview.py, cache.py, session.py, day.py, models.py,
breadcrumbs.py). Three of them also carried the same Plotly
"on_select" click-handler shape. Consolidating here:

  * `route_to(nav)` — write a `Navigation`'s params to the URL and rerun.
  * `handle_chart_drill(event, target_factory)` — read a Plotly
    selection event, build the drill target via the factory callable,
    and route there.

These helpers are deliberately tiny — they're glue, not domain logic.
Tests live where the callers do.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from tokenscope.log import get_logger
from tokenscope.navigation import Navigation

_log = get_logger(__name__)


# Session-state slot used to signal "this rerun was triggered by a
# programmatic navigation (i.e. `route_to`), NOT by a user clicking
# a navigation widget".
#
# The page-selector at `app.py:_render_page_selector` consumes this
# flag to decide whether the URL or the widget's persisted
# `session_state` slot is the source of truth on this run:
#
#   * Flag set  → URL wins. The radio's session_state is
#     overwritten BEFORE the widget instantiates so the drill
#     destination sticks (otherwise the radio's prior label
#     resurrects and bounces the user back).
#   * Flag clear → widget wins. A user-driven radio click delivers
#     the new value via `session_state` and must NOT be clobbered
#     by a URL-based sync on the same run.
#
# Lives next to `route_to` because the contract is "every
# `route_to` call MUST set this flag" — keeping the constant and
# the writer co-located prevents drift between them.
PROGRAMMATIC_NAV_FLAG = "_tokenscope_programmatic_nav_pending"


def route_to(
    target: Navigation,
    extra_params: dict[str, str] | None = None,
) -> None:
    """Write `target`'s params to `st.query_params` and rerun the app.

    Clears existing params first so stale fields (e.g. a leftover
    `block=` when navigating up to a session) don't stick around.

    `extra_params` is for non-Navigation URL state that the destination
    relies on — e.g. the Models view's "drill into a family" button
    seeds the sidebar's `models=` filter while routing to Overview.

    Sets `PROGRAMMATIC_NAV_FLAG` in `st.session_state` so the
    page-selector knows on the next render to sync its widget slot
    from the URL (programmatic nav) rather than from any
    user-click state (the radio's own click handling).
    """
    _log.info("nav.route target=%s extra_params=%s", target, extra_params or {})
    st.query_params.clear()
    for key, value in target.to_params().items():
        st.query_params[key] = value
    if extra_params:
        for key, value in extra_params.items():
            st.query_params[key] = value
    st.session_state[PROGRAMMATIC_NAV_FLAG] = True
    st.rerun()


def handle_chart_drill(
    event,
    target_factory: Callable[[str], Navigation],
    *,
    chart_key: str,
) -> None:
    """If the user clicked a Plotly selection, route to the drill target.

    `event` is what `st.plotly_chart(..., on_select="rerun")` returns —
    we read the first selected point and pass its label to
    `target_factory`. The factory chooses how to interpret the string
    (slice to YYYY-MM-DD for day drills, pass through for block ids).

    `chart_key` is the same string the caller passes to
    `st.plotly_chart(..., key=...)`. Required so every log line below
    is attributable to a specific chart — absence of "event received"
    log lines for a given chart is itself diagnostic data ("clicks
    aren't reaching us from that chart at all").

    Quietly does nothing for empty / non-point selections so the helper
    can be dropped inline next to the chart.
    """
    _log.debug(
        "chart.event.received chart=%s has_event=%s", chart_key, bool(event)
    )
    if not event:
        return
    selection = getattr(event, "selection", None)
    if not selection:
        _log.debug("chart.event.empty_selection chart=%s", chart_key)
        return
    points = getattr(selection, "points", None) or []
    if not points:
        _log.debug("chart.event.no_points chart=%s", chart_key)
        return
    raw = points[0].get("x") or points[0].get("y") or points[0].get("label")
    if not raw:
        _log.debug(
            "chart.event.point_missing_axes chart=%s point=%r",
            chart_key,
            points[0],
        )
        return
    _log.info("chart.drill chart=%s raw=%r", chart_key, raw)
    route_to(target_factory(str(raw)))
