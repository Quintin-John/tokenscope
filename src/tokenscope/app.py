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
from tokenscope.navigation import Navigation
from tokenscope.ui import block as block_view
from tokenscope.ui import day as day_view
from tokenscope.ui import overview, sidebar
from tokenscope.ui import session as session_view


def render() -> None:
    st.set_page_config(page_title="tokenscope", layout="wide")
    st.title("tokenscope")

    nav = Navigation.from_params(dict(st.query_params))
    state = sidebar.render()

    if nav.view == "day":
        day_view.render(state, nav)
    elif nav.view == "session":
        session_view.render(state, nav)
    elif nav.view == "block":
        block_view.render(state, nav)
    else:
        overview.render(state, nav)

    with st.sidebar:
        st.divider()
        try:
            version = get_ccusage_version()
            st.caption(f"ccusage `{version}`")
        except CcusageError as exc:
            st.error(f"ccusage bridge unavailable:\n\n```\n{exc}\n```")


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
