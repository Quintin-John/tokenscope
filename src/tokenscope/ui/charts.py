"""Plotly figure builders for the overview view.

These take a `DailyReport` (or pre-computed rows) and return a
`plotly.graph_objects.Figure`. They have no Streamlit dependency, so they
are unit-testable: build a figure, assert its traces / data look right.

When the report has no entries, builders return `None` — the UI layer
renders an empty-state message instead of an empty chart.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from tokenscope.analytics import (
    daily_cost_by_model,
    daily_token_mix,
    rolling_cost_average,
)
from tokenscope.models import DailyReport


def stacked_area_cost_by_family(daily_report: DailyReport) -> go.Figure | None:
    """Stacked area: per-day cost, coloured by model family (opus/haiku/...)."""
    rows = daily_cost_by_model(daily_report)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # Collapse same-day-same-family into one row so the area is a single band per family.
    grouped = df.groupby(["date", "family"], as_index=False)["cost"].sum()
    fig = px.area(
        grouped,
        x="date",
        y="cost",
        color="family",
        labels={"date": "Date", "cost": "Cost (USD)", "family": "Model family"},
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        legend_title_text="",
        yaxis_tickprefix="$",
    )
    return fig


def rolling_average_line(daily_report: DailyReport, window_days: int = 7) -> go.Figure | None:
    """7-day rolling average of daily cost as a line chart."""
    points = rolling_cost_average(daily_report, window_days=window_days)
    if not points:
        return None
    df = pd.DataFrame(points, columns=["date", "avg_cost"])
    fig = px.line(
        df,
        x="date",
        y="avg_cost",
        labels={"date": "Date", "avg_cost": f"{window_days}-day avg cost (USD)"},
    )
    fig.update_traces(mode="lines+markers")
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_tickprefix="$",
    )
    return fig


def token_mix_bar(daily_report: DailyReport) -> go.Figure | None:
    """Stacked bar: per-day input / output / cache_create / cache_read tokens."""
    rows = daily_token_mix(daily_report)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    fig = px.bar(
        df,
        x="date",
        y="tokens",
        color="kind",
        category_orders={"kind": ["input", "output", "cache_create", "cache_read"]},
        labels={"date": "Date", "tokens": "Tokens", "kind": ""},
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        legend_title_text="",
        barmode="stack",
    )
    return fig
