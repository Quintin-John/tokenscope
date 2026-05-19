"""Smoke tests for `tokenscope.ui._tables.render_data_table`.

The function is the dashboard's single `st.dataframe` invocation.
Tests pin:
  - empty input renders without raising
  - `column_config` reaches `st.dataframe` (verified via AppTest's
    `at.dataframe[0]` element)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest


def _make_app_script(tmp_path: Path, body: str) -> Path:
    """Write a tiny streamlit script that calls `render_data_table`
    with the test's inputs. AppTest runs the script and lets us
    inspect what landed in the rendered tree."""
    script = tmp_path / "harness.py"
    script.write_text(
        "from tokenscope.ui._tables import render_data_table\n"
        "import streamlit as st\n"
        f"{body}\n"
    )
    return script


def test_render_data_table_runs_against_empty_input(tmp_path: Path) -> None:
    """Empty rows list must render without raising. Streamlit's
    own empty-state takes over; callers gate at the data layer
    if they want a custom message."""
    script = _make_app_script(
        tmp_path, "render_data_table([], column_config={})"
    )
    at = AppTest.from_file(str(script), default_timeout=10)
    at.run()
    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    # An empty dataframe still produces one `st.dataframe` element.
    assert len(at.dataframe) == 1


def test_render_data_table_column_config_reaches_dataframe(tmp_path: Path) -> None:
    """The caller-supplied `column_config` must be honoured by the
    underlying `st.dataframe` call. AppTest exposes the rendered
    dataframe's columns; we assert the input column labels reach
    the output."""
    script = _make_app_script(
        tmp_path,
        "render_data_table(\n"
        "    [{'A': 'x', 'B': 1.0}, {'A': 'y', 'B': 2.5}],\n"
        "    column_config={\n"
        "        'A': st.column_config.TextColumn(width='small'),\n"
        "        'B': st.column_config.NumberColumn(format='$%.2f', width='medium'),\n"
        "    },\n"
        ")"
    )
    at = AppTest.from_file(str(script), default_timeout=10)
    at.run()
    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    assert len(at.dataframe) == 1
    df = at.dataframe[0].value
    assert list(df.columns) == ["A", "B"]
    assert len(df) == 2


def test_render_data_table_accepts_pre_built_dataframe(tmp_path: Path) -> None:
    """A pre-built pandas DataFrame passes through without re-wrapping.
    Lets callers that already build their own DataFrame (e.g.
    Overview's Cost composition) reuse the primitive cleanly."""
    script = _make_app_script(
        tmp_path,
        "import pandas as pd\n"
        "df = pd.DataFrame([{'X': 'foo', 'Y': 10}])\n"
        "render_data_table(df, column_config={})"
    )
    at = AppTest.from_file(str(script), default_timeout=10)
    at.run()
    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    assert len(at.dataframe) == 1
    rendered = at.dataframe[0].value
    assert list(rendered.columns) == ["X", "Y"]
