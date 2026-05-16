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

import streamlit as st

from tokenscope.ccusage import CcusageError, get_ccusage_version
from tokenscope.navigation import TOP_LEVEL_VIEWS, Navigation, ViewName
from tokenscope.ui import block as block_view
from tokenscope.ui import cache as cache_view
from tokenscope.ui import day as day_view
from tokenscope.ui import live as live_view
from tokenscope.ui import models as models_view
from tokenscope.ui import overview, sidebar
from tokenscope.ui import session as session_view


_VIEW_LABELS: dict[ViewName, str] = {
    "overview": "Overview",
    "live": "Live",
    "cache": "Cache",
    "models": "Models",
}


def render() -> None:
    st.set_page_config(page_title="tokenscope", layout="wide")
    st.title("tokenscope")

    nav = Navigation.from_params(dict(st.query_params))
    state = sidebar.render()

    # Top-level page selector. Drill views (day/session/block) hide it in
    # favour of their breadcrumb trail.
    if nav.view in TOP_LEVEL_VIEWS:
        nav = _render_page_selector(nav)

    if nav.view == "day":
        day_view.render(state, nav)
    elif nav.view == "session":
        session_view.render(state, nav)
    elif nav.view == "block":
        block_view.render(state, nav)
    elif nav.view == "cache":
        cache_view.render(state, nav)
    elif nav.view == "models":
        models_view.render(state, nav)
    elif nav.view == "live":
        live_view.render(state, nav)
    else:
        overview.render(state, nav)

    with st.sidebar:
        st.divider()
        try:
            version = get_ccusage_version()
            st.caption(f"ccusage `{version}`")
        except CcusageError as exc:
            st.error(f"ccusage bridge unavailable:\n\n```\n{exc}\n```")


def _render_page_selector(nav: Navigation) -> Navigation:
    label_to_view = {v: k for k, v in _VIEW_LABELS.items()}
    options = [_VIEW_LABELS[v] for v in TOP_LEVEL_VIEWS]
    current_label = _VIEW_LABELS[nav.view]  # safe: caller checked TOP_LEVEL_VIEWS
    chosen_label = st.radio(
        "Page",
        options=options,
        index=options.index(current_label),
        horizontal=True,
        label_visibility="collapsed",
        key="top-page-selector",
    )
    chosen_view: ViewName = label_to_view[chosen_label]
    if chosen_view != nav.view:
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
