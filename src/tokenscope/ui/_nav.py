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

from tokenscope.navigation import Navigation


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
    """
    st.query_params.clear()
    for key, value in target.to_params().items():
        st.query_params[key] = value
    if extra_params:
        for key, value in extra_params.items():
            st.query_params[key] = value
    st.rerun()


def handle_chart_drill(
    event,
    target_factory: Callable[[str], Navigation],
) -> None:
    """If the user clicked a Plotly selection, route to the drill target.

    `event` is what `st.plotly_chart(..., on_select="rerun")` returns —
    we read the first selected point and pass its label to
    `target_factory`. The factory chooses how to interpret the string
    (slice to YYYY-MM-DD for day drills, pass through for block ids).

    Quietly does nothing for empty / non-point selections so the helper
    can be dropped inline next to the chart.
    """
    if not event:
        return
    selection = getattr(event, "selection", None)
    if not selection:
        return
    points = getattr(selection, "points", None) or []
    if not points:
        return
    raw = points[0].get("x") or points[0].get("y") or points[0].get("label")
    if not raw:
        return
    route_to(target_factory(str(raw)))
