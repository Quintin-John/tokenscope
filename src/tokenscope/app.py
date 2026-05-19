"""Streamlit application entry point for tokenscope-viz.

Two ways this module is executed:
  1. `streamlit run src/tokenscope/app.py` — streamlit imports the script,
     its runtime is active, and `render()` is called at the bottom.
  2. `tokenscope` console script — `main()` re-execs streamlit on this file.

Tests import this module without streamlit running, so `render()` is skipped
and importing has no side effects.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

import streamlit as st

from tokenscope.ccusage import CcusageError, get_ccusage_version
from tokenscope.log import get_logger, setup_logging
from tokenscope.navigation import (
    TOP_LEVEL_LABELS,
    TOP_LEVEL_VIEWS,
    VALID_VIEWS,
    Navigation,
    ViewName,
)
from tokenscope.ui import block as block_view
from tokenscope.ui import cache as cache_view
from tokenscope.ui import daily as daily_view
from tokenscope.ui import day as day_view
from tokenscope.ui import live as live_view
from tokenscope.ui import models as models_view
from tokenscope.ui import overview, sidebar
from tokenscope.ui import session as session_view
from tokenscope.ui._nav import PROGRAMMATIC_NAV_FLAG
from tokenscope.ui.sidebar import SidebarState

# Hardcoded module name: app.py is launched as `__main__` by both
# `streamlit run` and pytest's AppTest (runpy semantics), so
# `__name__` would not start with "tokenscope" and the log records
# would land outside our logger hierarchy.
_log = get_logger("tokenscope.app")


# View → renderer registry. Single dispatch source replaces the prior
# 14-line if/elif chain in `render()`. Adding a new view requires one
# entry here AND one entry in `navigation._VIEWS`; the assertion below
# catches drift between the two at module-load time.
#
# Display labels for the page selector live in `navigation.TOP_LEVEL_LABELS`
# (derived from the same registry that owns view names), not here —
# renderers and labels are independent concerns kept in their respective
# modules.
_RENDERERS: dict[ViewName, Callable[[SidebarState, Navigation], None]] = {
    "overview": overview.render,
    "live": live_view.render,
    "cache": cache_view.render,
    "models": models_view.render,
    "daily": daily_view.render,
    "day": day_view.render,
    "session": session_view.render,
    "block": block_view.render,
}


# Drift guard: every declared view must have a renderer, and no orphan
# renderers may exist. Catches "added a `_ViewMeta` but forgot the
# renderer" (or vice versa) at module-load time rather than at the
# first user request that hits the missing dispatch arm.
assert set(_RENDERERS) == set(VALID_VIEWS), (
    f"_RENDERERS / VALID_VIEWS mismatch: "
    f"missing={set(VALID_VIEWS) - set(_RENDERERS)!r}, "
    f"extra={set(_RENDERERS) - set(VALID_VIEWS)!r}"
)


# App-wide CSS lives in `src/tokenscope/ui/_app_styles.css`. Read once
# at import; injected on every render via `st.markdown(unsafe_allow_html)`.
# Covers: page-nav tab restyle, card containers, code-pill suppression,
# responsive column-flex rules, Overview H1 polish, insight callout.
_APP_CSS = (
    Path(__file__).parent / "ui" / "_app_styles.css"
).read_text()


def _build_about_text() -> str:
    """Markdown for the About entry in Streamlit's hamburger menu.

    Surfaces the upstream ccusage version (previously in a sidebar
    footer with code-pill styling — moved out so the panel can stop
    looking like CLI documentation) plus the timezone-override note
    that used to live as an inline caption in the sidebar.

    `get_ccusage_version` raises on bridge failure; we render a
    fallback rather than let the page-config call propagate the error.
    """
    try:
        version_line = f"ccusage version: {get_ccusage_version()}"
    except CcusageError:
        version_line = "ccusage version: unavailable"
    return (
        "**tokenscope** — local-only dashboard for Claude Code spend.\n\n"
        f"{version_line}\n\n"
        "Times shown in your auto-detected system timezone. To override, "
        "set the `TZ` environment variable before launching."
    )


def render() -> None:
    setup_logging()
    st.set_page_config(
        page_title="tokenscope",
        layout="wide",
        menu_items={"About": _build_about_text()},
    )
    st.markdown(f"<style>{_APP_CSS}</style>", unsafe_allow_html=True)

    # Defensive: Streamlit's PlotlyChart bundle has been observed to
    # emit `Unhandled Promise Rejection: undefined` during the
    # selection-event setup phase on charts that pass `on_select`.
    # The rejection has no value (`reject()` called with no argument
    # → `undefined`) and the wrapper renders the rejection into the
    # chart legend as a phantom entry. The figure-side fix is
    # `clickmode="event+select"` (set in `apply_enterprise_style`);
    # this listener is the belt-and-braces backup — silently swallow
    # any `undefined`/`null` promise rejection so it can't surface in
    # the DOM. Real errors (rejections with a value) propagate
    # normally so we don't mask actionable JS failures.
    st.markdown(
        """
        <script>
        window.addEventListener('unhandledrejection', function (event) {
            if (event.reason === undefined || event.reason === null) {
                event.preventDefault();
            }
        });
        </script>
        """,
        unsafe_allow_html=True,
    )

    # NB: no visible `st.title("tokenscope")`. The product wordmark
    # lives in the browser tab (`page_title`); the H1 on each page is
    # the *view name*, rendered by the view itself (`# Overview`,
    # `# Live`, etc.). This avoids the inverted hierarchy where the
    # product name dominates every page.

    nav = Navigation.from_params(dict(st.query_params))
    _log.debug("app.render view=%s", nav.view)
    state = sidebar.render()

    # Page selector is rendered on EVERY view (was previously hidden on
    # drill views, which trapped users — see slice 11). Drill views just
    # have no highlight; the user can always click out to a top-level
    # view in one tap.
    nav = _render_page_selector(nav)

    # Registry dispatch — single source of truth (see `_RENDERERS`
    # above). `Navigation.from_params` clamps unknown values to
    # "overview", so the lookup is guaranteed to hit.
    _RENDERERS[nav.view](state, nav)


_PAGE_SELECTOR_KEY = "top-page-selector"


def _render_page_selector(nav: Navigation) -> Navigation:
    """Render the top-level page selector. Always visible — on drill views
    (day/session/block) the selector has no current selection, so picking
    any option routes out cleanly.

    Bug previously surfaced (diagnosed via the logging slice):

        chart.drill chart=overview-token-mix raw='2026-04-24'
        nav.route target=Navigation(view='day', ...)
        app.render view=day        ← drill succeeded
        app.render view=overview   ← but instantly reverted by THIS function

    Cause: `st.radio(..., key=K)` reads `st.session_state[K]` AHEAD of
    the `index=` argument. Once the user had interacted with the page
    selector in any prior render, `st.session_state["top-page-selector"]`
    held a top-level label (e.g. "Overview"). On the next render for a
    drill view, the radio resurrected that label, the if-clause below
    saw `chosen_view != nav.view`, and routed the user back out of the
    drill they just navigated into.

    Fix: before rendering, drop the persisted selection whenever the
    current nav is NOT a top-level view. The radio then renders with
    `index=None`, returns `None`, and we no-op out as designed. The
    user can still explicitly click a top-level option to leave the
    drill — that re-populates session_state and routes via the normal
    `chosen_view != nav.view` path.
    """
    # URL-as-source-of-truth applies ONLY when this run was triggered
    # by a programmatic navigation (e.g. the Models view's "View opus
    # in Overview →" drill calls `route_to`). The flag is set inside
    # `route_to` and consumed (popped) here BEFORE the radio widget
    # instantiates — that's the one moment we can legally assign to a
    # widget-keyed session_state slot.
    #
    # When the flag is NOT set, the rerun was triggered by the user
    # clicking the radio itself: Streamlit has already written the
    # chosen label into `session_state[_PAGE_SELECTOR_KEY]`, and the
    # `chosen_view != nav.view` branch below picks that up and routes
    # there. Touching `session_state` on that path would CLOBBER the
    # click (which is exactly the regression the previous version of
    # this code introduced).
    programmatic_nav = st.session_state.pop(PROGRAMMATIC_NAV_FLAG, False)
    if programmatic_nav:
        if nav.view in TOP_LEVEL_VIEWS:
            st.session_state[_PAGE_SELECTOR_KEY] = TOP_LEVEL_LABELS[nav.view]
        else:
            # Programmatic nav into a drill view — clear the persisted
            # top-level label so the radio renders with no selection,
            # otherwise a subsequent click would bounce back to a
            # stale top-level.
            st.session_state.pop(_PAGE_SELECTOR_KEY, None)
    elif nav.view not in TOP_LEVEL_VIEWS:
        # Arrived at a drill view via URL (paste, bookmark) with a
        # stale top-level label still in session_state. Clear so the
        # radio shows no selection. The user can re-click any
        # top-level option to leave the drill via the normal
        # `chosen_view != nav.view` path below.
        st.session_state.pop(_PAGE_SELECTOR_KEY, None)

    label_to_view = {v: k for k, v in TOP_LEVEL_LABELS.items()}
    options = [TOP_LEVEL_LABELS[v] for v in TOP_LEVEL_VIEWS]
    index = options.index(TOP_LEVEL_LABELS[nav.view]) if nav.view in TOP_LEVEL_VIEWS else None
    chosen_label = st.radio(
        "Page",
        options=options,
        index=index,
        horizontal=True,
        label_visibility="collapsed",
        key=_PAGE_SELECTOR_KEY,
    )
    if chosen_label is None:
        # User hasn't picked anything yet (drill view, fresh render). No-op.
        return nav
    chosen_view: ViewName = label_to_view[chosen_label]
    if chosen_view != nav.view:
        _log.info("nav.page_selector from=%s to=%s", nav.view, chosen_view)
        st.query_params.clear()
        st.query_params["view"] = chosen_view
        st.rerun()
    return nav


def main() -> None:
    """Console-script entry: re-exec under `streamlit run`, bound to localhost only."""
    script = Path(__file__).resolve()
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(script),
        "--server.address=127.0.0.1",
        "--browser.gatherUsageStats=false",
        *sys.argv[1:],
    ]
    raise SystemExit(subprocess.call(cmd))


def _streamlit_runtime_active() -> bool:
    try:
        from streamlit.runtime import exists
    except ImportError:
        return False
    return exists()


if _streamlit_runtime_active():
    render()
