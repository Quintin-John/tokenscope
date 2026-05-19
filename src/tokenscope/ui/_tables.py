"""Shared render primitive for every data table on the dashboard.

Consumers as of this slice: `overview._render_cost_composition`
(Cost composition table) and `daily._render_unified_table` (Daily
unified table). One `st.dataframe` invocation, one set of layout
parameters — adding a third data-table consumer requires zero new
layout decisions and inherits the same visual language (fonts,
widths, sizing, alignment).

This module is intentionally tiny. Its single responsibility is
"the one canonical `st.dataframe` call" — if it grows beyond that
it's accidentally accumulating table-shaping logic that belongs
in the caller.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


def render_data_table(
    rows: list[dict] | pd.DataFrame,
    column_config: dict[str, Any],
) -> None:
    """Render `rows` as a Streamlit dataframe using the dashboard's
    one canonical invocation: full-width container, no index column,
    caller-supplied per-column config.

    Lists of dicts are normalised to a pandas DataFrame in-flight;
    pre-built DataFrames pass through. Empty input renders
    Streamlit's own empty-state — callers gate at the data layer
    if they want a different empty-state message.
    """
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config=column_config,
    )
