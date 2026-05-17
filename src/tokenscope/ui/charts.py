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


def burn_gauge(
    block: BlockEntry, typical: float | None = None
) -> go.Figure | None:
    """Burn-rate gauge: actual cost-per-hour with projected end-of-window cost as a delta.

    When `typical` is provided (median burn from completed historical
    blocks), a red threshold line is drawn at that value — gives users an
    instant "above/below my usual" read instead of asking them to remember
    what their typical burn looks like.

    Returns None when the block has no burn rate (gap block or finished block).
    """
    if block.burn_rate is None:
        return None
    projected = block.projection.total_cost if block.projection else None
    delta = {"reference": projected, "valueformat": "$,.2f"} if projected else None
    gauge: dict = {
        "axis": {"tickprefix": "$"},
        "bar": {"color": "#1f77b4"},
    }
    if typical is not None and typical > 0:
        gauge["threshold"] = {
            "line": {"color": "#d62728", "width": 3},
            "thickness": 0.85,
            "value": typical,
        }
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number" + ("+delta" if delta else ""),
            value=block.burn_rate.cost_per_hour,
            number={"prefix": "$", "valueformat": ",.2f", "suffix": "/hr"},
            delta=delta,
            gauge=gauge,
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


def single_family_token_bar(daily_report: DailyReport) -> go.Figure | None:
    """Horizontal bar of total tokens per kind, for a window where only
    one model family is present.

    A Sankey with one right-side node is just a four-strand comb feeding
    one label — adds visual ceremony without insight. This bar gives the
    same information honestly. Uses log x-axis for the same reason the
    daily token-mix bar does (cache_read swamps everything else). Each
    kind gets its own colour from Plotly's default qualitative palette
    so the four bars read as distinct categories at a glance.
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
    df = pd.DataFrame({"kind": kinds, "tokens": [totals[k] for k in kinds]})
    fig = px.bar(
        df,
        x="tokens",
        y="kind",
        color="kind",
        orientation="h",
        category_orders={"kind": kinds},
        labels={"tokens": "Tokens", "kind": ""},
    )
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>%{x:,.0f} tokens<extra></extra>",
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_type="log",
        height=260,
        showlegend=False,
    )
    return fig


def token_flow_sankey(
    daily_report: DailyReport,
    *,
    value_mode: str = "tokens",
    top_n: int | None = None,
) -> go.Figure | None:
    """Sankey: token-kind → model family.

    ``value_mode``:
      * ``"tokens"`` — link widths proportional to token counts.
      * ``"cost"``  — link widths proportional to per-family cost,
        proportionally attributed across kinds. Total Sankey width then
        equals total window cost.

    ``top_n`` collapses smaller families into an "Others" node.

    Family labels always carry the family's aggregate cost. Hover detail
    shows both the raw token count and the family's total cost so the
    user reads both dimensions regardless of which mode is active.
    """
    data_ = token_flow_sankey_data(daily_report, value_mode=value_mode, top_n=top_n)
    if not data_["values"]:
        return None
    customdata = data_["customdata"]
    value_label = "Cost share" if value_mode == "cost" else "Tokens"
    value_format = "$,.2f" if value_mode == "cost" else ",d"
    link_hover = (
        "<b>%{source.label}</b> → <b>%{target.label}</b><br>"
        f"{value_label}: %{{value:{value_format}}}<br>"
        "Tokens (absolute): %{customdata[0]:,d}<br>"
        "Family total cost: $%{customdata[1]:,.2f}<extra></extra>"
    )
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
                customdata=customdata,
                hovertemplate=link_hover,
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


def session_blocks_timeline(
    blocks: list[BlockEntry],
    tz: str | None = None,
) -> go.Figure | None:
    """Horizontal timeline: each block as a bar from its start to its end.

    Used on the session detail view to close PLAN.md §3.1's
    "Blocks within session" drill. Active blocks render in a brighter
    colour so the live one stands out from the completed ones.

    Hover carries block id, cost, and total tokens. When ``tz`` is
    given, the chart's x-axis displays timestamps in the user's
    timezone; otherwise UTC.

    Returns None for an empty list — the caller renders empty-state copy.
    """
    if not blocks:
        return None
    rows = []
    for b in blocks:
        rows.append(
            {
                "block_id": b.id,
                "start": b.start_time,
                "end": b.end_time,
                "label": "Active" if b.is_active else "Completed",
                "cost": b.cost_usd,
                "tokens": b.total_tokens,
            }
        )
    df = pd.DataFrame(rows)
    fig = px.timeline(
        df,
        x_start="start",
        x_end="end",
        y="block_id",
        color="label",
        category_orders={"label": ["Active", "Completed"]},
        hover_data={
            "block_id": False,
            "start": False,
            "end": False,
            "label": False,
            "cost": ":$,.2f",
            "tokens": ":,.0f",
        },
    )
    fig.update_yaxes(autorange="reversed")  # newest at top
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=max(180, 40 * len(blocks) + 60),
        legend_title_text="",
        xaxis_title=f"Time ({tz})" if tz else "Time (UTC)",
        yaxis_title="",
    )
    return fig
