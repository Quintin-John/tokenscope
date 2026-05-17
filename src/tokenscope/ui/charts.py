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
    daily_cache_hit_ratio,
    daily_cost_by_model,
    daily_dollars_saved,
    daily_token_mix,
    rolling_cost_average,
    token_flow_sankey_data,
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


def cache_hit_ratio_line(daily_report: DailyReport) -> go.Figure | None:
    """Per-day cache hit ratio line chart (y-axis 0–100%)."""
    series = daily_cache_hit_ratio(daily_report)
    if not series:
        return None
    df = pd.DataFrame(series, columns=["date", "ratio"])
    fig = px.line(
        df,
        x="date",
        y="ratio",
        labels={"date": "Date", "ratio": "Cache hit ratio"},
    )
    fig.update_traces(mode="lines+markers")
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(tickformat=".0%", range=[0, 1]),
    )
    return fig


def dollars_saved_bar(daily_report: DailyReport) -> go.Figure | None:
    """Stacked bar of estimated $ saved per day, coloured by model family."""
    rows = daily_dollars_saved(daily_report)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # Collapse same-day-same-family rows so the bars are one band per family.
    grouped = df.groupby(["date", "family"], as_index=False)["dollars_saved"].sum()
    fig = px.bar(
        grouped,
        x="date",
        y="dollars_saved",
        color="family",
        labels={"date": "Date", "dollars_saved": "Estimated $ saved", "family": ""},
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_tickprefix="$",
        barmode="stack",
        legend_title_text="",
    )
    return fig


def single_family_token_bar(daily_report: DailyReport) -> go.Figure | None:
    """Horizontal bar of total tokens per kind, for a window where only
    one model family is present.

    A Sankey with one right-side node is just a four-strand comb feeding
    one label — adds visual ceremony without insight. This bar gives the
    same information honestly. Uses log x-axis for the same reason the
    daily token-mix bar does (cache_read swamps everything else).
    """
    if not daily_report.daily:
        return None
    totals = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    for entry in daily_report.daily:
        totals["input"] += entry.input_tokens
        totals["output"] += entry.output_tokens
        totals["cache_create"] += entry.cache_creation_tokens
        totals["cache_read"] += entry.cache_read_tokens
    if not any(totals.values()):
        return None
    kinds = ["cache_read", "cache_create", "output", "input"]
    values = [totals[k] for k in kinds]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=kinds,
            orientation="h",
            hovertemplate="<b>%{y}</b><br>%{x:,.0f} tokens<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_type="log",
        xaxis_title="Tokens",
        yaxis_title="",
        height=260,
    )
    return fig


def token_flow_sankey(daily_report: DailyReport) -> go.Figure | None:
    """Sankey: token-kind → model family. Family labels carry the family's cost."""
    data_ = token_flow_sankey_data(daily_report)
    if not data_["values"]:
        return None
    fig = go.Figure(
        go.Sankey(
            node=dict(
                label=data_["labels"],
                pad=18,
                thickness=18,
            ),
            link=dict(
                source=data_["sources"],
                target=data_["targets"],
                value=data_["values"],
            ),
        )
    )
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), height=520)
    return fig


def token_mix_bar(daily_report: DailyReport) -> go.Figure | None:
    """Per-day token-mix bar chart.

    cache_read is typically 100–100,000× larger than input / output /
    cache_create. Two combined fixes keep every kind readable:

    1. **barmode="overlay"** with categories ordered largest-typical-first
       so cache_read paints to the back and the smaller kinds layer on top.
       Bars share a 0.75 opacity so neither side fully occludes the other.
    2. **log y-axis** (`yaxis_type="log"`). On a linear axis, an input bar
       of ~1k tokens next to a cache_read bar of ~100M is a single pixel —
       overlay z-order doesn't help when the value itself is invisible.
       Log scale turns those into bars of substantially different but
       comparable heights.
    """
    rows = daily_token_mix(daily_report)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    fig = px.bar(
        df,
        x="date",
        y="tokens",
        color="kind",
        # Largest-typical first → drawn first → at the back of the overlay
        # z-order. Smaller kinds render on top and stay visible.
        category_orders={"kind": ["cache_read", "cache_create", "output", "input"]},
        labels={"date": "Date", "tokens": "Tokens", "kind": ""},
    )
    # Hover shows the absolute token count even though the y-axis is log.
    # Log scale makes magnitudes comparable visually; hover keeps the truth
    # one mouse-move away.
    fig.update_traces(
        opacity=0.75,
        hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{y:,.0f} tokens<extra></extra>",
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        legend_title_text="",
        barmode="overlay",
        yaxis_type="log",
    )
    return fig
