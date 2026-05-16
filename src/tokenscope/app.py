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


def render() -> None:
    """Render the phase-1 dashboard shell. Real charts come in later phases."""
    st.set_page_config(page_title="tokenscope", layout="wide")
    st.title("tokenscope")
    st.caption("Phase 1 scaffolding — no charts yet. See PLAN.md.")

    try:
        version = get_ccusage_version()
    except CcusageError as exc:
        st.error(f"ccusage bridge unavailable:\n\n```\n{exc}\n```")
        return
    st.success(f"ccusage bridge OK — version `{version}`")


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
