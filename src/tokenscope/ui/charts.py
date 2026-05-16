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
    cost_share_by_model,
    daily_cost_by_model,
    daily_token_mix,
    rolling_cost_average,
)
from tokenscope.models import BlockEntry, DailyEntry, DailyReport, SessionEntry


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


def donut_cost_by_model(entry: DailyEntry | SessionEntry) -> go.Figure | None:
    """Donut: cost share by model for a single day or session.

    Returns None when the entry has no model breakdowns (defensive — every
    entry ccusage emits has at least one in practice).
    """
    rows = cost_share_by_model(entry)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    fig = px.pie(
        df,
        values="cost",
        names="model",
        hole=0.55,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), showlegend=True)
    return fig


def session_token_mix(entry: SessionEntry) -> go.Figure:
    """Stacked bar of input / output / cache_create / cache_read for one session."""
    rows = [
        {"kind": "input", "tokens": entry.input_tokens},
        {"kind": "output", "tokens": entry.output_tokens},
        {"kind": "cache_create", "tokens": entry.cache_creation_tokens},
        {"kind": "cache_read", "tokens": entry.cache_read_tokens},
    ]
    df = pd.DataFrame(rows)
    fig = px.bar(
        df,
        x="kind",
        y="tokens",
        color="kind",
        category_orders={"kind": ["input", "output", "cache_create", "cache_read"]},
        labels={"kind": "", "tokens": "Tokens"},
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        showlegend=False,
    )
    return fig


def burn_gauge(block: BlockEntry) -> go.Figure | None:
    """Burn-rate gauge: actual cost-per-hour, with projected end-of-window cost as a delta.

    Returns None when the block has no burn rate (gap block or finished block).
    """
    if block.burn_rate is None:
        return None
    projected = block.projection.total_cost if block.projection else None
    delta = {"reference": projected, "valueformat": "$,.2f"} if projected else None
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number" + ("+delta" if delta else ""),
            value=block.burn_rate.cost_per_hour,
            number={"prefix": "$", "valueformat": ",.2f", "suffix": "/hr"},
            delta=delta,
            gauge={
                "axis": {"tickprefix": "$"},
                "bar": {"color": "#1f77b4"},
            },
            title={"text": "Burn rate"},
        )
    )
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=320)
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
