"""Tests for tokenscope.ui.charts — Plotly figure builders.

These tests don't require a Streamlit runtime; the builders return
plotly.graph_objects.Figure objects we can inspect directly. The goal is
not to pin down Plotly internals — just to confirm that the data we
shaped flows through and that empty inputs short-circuit cleanly.
"""

from __future__ import annotations

import plotly.graph_objects as go

from tokenscope.models import (
    BlockEntry,
    BlockTokenCounts,
    BurnRate,
    DailyEntry,
    DailyReport,
    ModelBreakdown,
    Projection,
    SessionEntry,
    Totals,
)
from tokenscope.ui.charts import (
    _daily_metric_figure,
    burn_gauge,
    cache_hit_ratio_line,
    donut_cost_by_model,
    rolling_average_line,
    session_blocks_timeline,
    session_token_mix,
    single_family_token_bar,
    stacked_area_cost_by_family,
    token_flow_sankey,
    token_mix_bar,
)


def _entry(date_str: str, *, cost: float, model: str) -> DailyEntry:
    return DailyEntry(
        date=date_str,
        inputTokens=100,
        outputTokens=200,
        cacheCreationTokens=300,
        cacheReadTokens=400,
        totalTokens=1000,
        totalCost=cost,
        modelsUsed=[model],
        modelBreakdowns=[
            ModelBreakdown(
                modelName=model,
                inputTokens=100,
                outputTokens=200,
                cacheCreationTokens=300,
                cacheReadTokens=400,
                cost=cost,
            )
        ],
    )


def _report(entries: list[DailyEntry]) -> DailyReport:
    return DailyReport(
        daily=entries,
        totals=Totals(
            inputTokens=sum(e.input_tokens for e in entries),
            outputTokens=sum(e.output_tokens for e in entries),
            cacheCreationTokens=sum(e.cache_creation_tokens for e in entries),
            cacheReadTokens=sum(e.cache_read_tokens for e in entries),
            totalTokens=sum(e.total_tokens for e in entries),
            totalCost=sum(e.total_cost for e in entries),
        ),
    )


# ---------- _daily_metric_figure (the single-day fallback helper) ----------


def _two_day_df():
    import pandas as pd
    return pd.DataFrame(
        [
            {"date": "2026-05-15", "y": 1.0, "g": "a"},
            {"date": "2026-05-15", "y": 2.0, "g": "b"},
            {"date": "2026-05-16", "y": 3.0, "g": "a"},
            {"date": "2026-05-16", "y": 4.0, "g": "b"},
        ]
    )


def _one_day_df():
    import pandas as pd
    return pd.DataFrame(
        [
            {"date": "2026-05-16", "y": 5.0, "g": "a"},
            {"date": "2026-05-16", "y": 6.0, "g": "b"},
        ]
    )


def test_daily_metric_figure_multi_day_area_uses_stackgroup() -> None:
    fig = _daily_metric_figure(
        _two_day_df(),
        x="date", y="y", color="g",
        labels={"date": "Date", "y": "Y", "g": "G"},
        multi_day="area",
    )
    # px.area renders scatter traces with stackgroup set (that's how
    # plotly_express distinguishes "area" from "line" — fill is None,
    # the stackgroup attr carries the layering identity).
    assert all(t.type == "scatter" for t in fig.data)
    assert all(t.stackgroup for t in fig.data), (
        f"expected stackgroup on every trace, got "
        f"{[t.stackgroup for t in fig.data]}"
    )


def test_daily_metric_figure_multi_day_line_has_markers_and_lines() -> None:
    fig = _daily_metric_figure(
        _two_day_df()[["date", "y"]].drop_duplicates(subset=["date"]),
        x="date", y="y",
        labels={"date": "Date", "y": "Y"},
        multi_day="line",
    )
    assert len(fig.data) == 1
    assert fig.data[0].type == "scatter"
    assert fig.data[0].mode == "lines+markers"


def test_daily_metric_figure_single_day_forces_bar_with_stack_when_coloured() -> None:
    fig = _daily_metric_figure(
        _one_day_df(),
        x="date", y="y", color="g",
        labels={"date": "Date", "y": "Y", "g": "G"},
        multi_day="area",
    )
    assert all(t.type == "bar" for t in fig.data)
    assert fig.layout.barmode == "stack"


def test_daily_metric_figure_single_day_uncoloured_bar_no_stack_directive() -> None:
    """When ``color`` is None there's no series split, so barmode must not
    be touched — leaving Plotly's default is the correct authoritative
    behaviour (no spurious layout override)."""
    fig = _daily_metric_figure(
        _one_day_df()[["date", "y"]].drop_duplicates(subset=["date"]),
        x="date", y="y",
        labels={"date": "Date", "y": "Y"},
        multi_day="line",
    )
    assert len(fig.data) == 1
    assert fig.data[0].type == "bar"
    # We did not set barmode in this branch — Plotly default ('group') stands.
    assert fig.layout.barmode != "stack"


# ---------- stacked_area_cost_by_family / rolling_average_line ----------


def test_stacked_area_returns_figure_with_family_traces() -> None:
    report = _report(
        [
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=1.0, model="claude-haiku-4-5-20251001"),
            _entry("2026-05-16", cost=4.0, model="claude-opus-4-7"),
        ]
    )
    fig = stacked_area_cost_by_family(report)
    assert isinstance(fig, go.Figure)
    trace_names = {t.name for t in fig.data}
    assert trace_names == {"opus", "haiku"}


def test_stacked_area_empty_returns_none() -> None:
    assert stacked_area_cost_by_family(_report([])) is None


def test_stacked_area_single_day_uses_bar_not_area() -> None:
    """Single-day windows must render as a stacked bar — px.area paints
    a zero-width band with one x-value, leaving the chart blank."""
    report = _report(
        [
            _entry("2026-05-16", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=1.0, model="claude-haiku-4-5-20251001"),
        ]
    )
    fig = stacked_area_cost_by_family(report)
    assert isinstance(fig, go.Figure)
    # All traces should be bars, not scatter (which is what px.area uses).
    assert all(t.type == "bar" for t in fig.data), (
        f"expected bar traces on single-day window, got "
        f"{[t.type for t in fig.data]}"
    )
    # Stacked, not grouped — barmode must be "stack" so families layer.
    assert fig.layout.barmode == "stack"
    # The family colour split must survive the fallback.
    assert {t.name for t in fig.data} == {"opus", "haiku"}


def test_rolling_line_returns_figure() -> None:
    report = _report(
        [
            _entry("2026-05-13", cost=1.0, model="claude-opus-4-7"),
            _entry("2026-05-14", cost=3.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
        ]
    )
    fig = rolling_average_line(report, window_days=2)
    assert isinstance(fig, go.Figure)
    # Single trace = the rolling-average line.
    assert len(fig.data) == 1
    ys = list(fig.data[0].y)
    assert ys == [1.0, 2.0, 4.0]  # 1, (1+3)/2, (3+5)/2


def test_rolling_line_single_day_uses_bar_not_line() -> None:
    """Single-day rolling average must render as a bar — a one-point
    px.line is just a marker dot that is easy to miss next to a $-axis."""
    report = _report([_entry("2026-05-16", cost=42.0, model="claude-opus-4-7")])
    fig = rolling_average_line(report, window_days=7)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    assert fig.data[0].type == "bar", (
        f"expected bar trace on single-day window, got {fig.data[0].type}"
    )
    assert list(fig.data[0].y) == [42.0]


def test_rolling_line_empty_returns_none() -> None:
    assert rolling_average_line(_report([]), window_days=7) is None


def test_token_mix_bar_has_four_kinds() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_bar(report)
    assert isinstance(fig, go.Figure)
    kinds = {t.name for t in fig.data}
    assert kinds == {"input", "output", "cache_create", "cache_read"}


def test_token_mix_bar_overlay_log_scale() -> None:
    """Regression: cache_read is typically 100–100,000× larger than the
    other kinds. Without a log y-axis, the smaller kinds collapse to a
    single pixel even on overlay mode."""
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_bar(report)
    assert fig.layout.barmode == "overlay"
    # cache_read at the back of the z-order.
    assert fig.data[0].name == "cache_read"
    # Sub-1.0 opacity so the back layer remains visible under the front bars.
    for trace in fig.data:
        assert trace.opacity is not None and trace.opacity < 1.0
    # Log scale so the smaller kinds aren't pixel-thin.
    assert fig.layout.yaxis.type == "log"


def test_token_mix_bar_hovertemplate_shows_absolute_tokens() -> None:
    """Log scale hides the linear truth — hover keeps the raw count one
    mouse-move away."""
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_bar(report)
    for trace in fig.data:
        assert trace.hovertemplate is not None
        assert "tokens" in trace.hovertemplate.lower()
        assert "%{y" in trace.hovertemplate  # raw y-value, not log10


def test_token_mix_bar_empty_returns_none() -> None:
    assert token_mix_bar(_report([])) is None


# ---------- donut_cost_by_model ----------


def test_donut_cost_by_model_returns_figure() -> None:
    entry = _entry("2026-05-16", cost=10.0, model="claude-opus-4-7")
    fig = donut_cost_by_model(entry)
    assert isinstance(fig, go.Figure)
    # One pie trace, with one slice per model.
    assert len(fig.data) == 1
    assert list(fig.data[0].labels) == ["claude-opus-4-7"]
    assert list(fig.data[0].values) == [10.0]


def test_donut_cost_by_model_no_breakdowns_returns_none() -> None:
    entry = DailyEntry(
        date="2026-05-16",
        inputTokens=0,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=0,
        totalTokens=0,
        totalCost=0.0,
        modelsUsed=[],
        modelBreakdowns=[],
    )
    assert donut_cost_by_model(entry) is None


# ---------- session_token_mix ----------


def _session(*, cost: float = 1.0) -> SessionEntry:
    return SessionEntry(
        sessionId="sess-1",
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        totalTokens=100,
        totalCost=cost,
        modelsUsed=["claude-opus-4-7"],
        modelBreakdowns=[
            ModelBreakdown(
                modelName="claude-opus-4-7",
                inputTokens=10,
                outputTokens=20,
                cacheCreationTokens=30,
                cacheReadTokens=40,
                cost=cost,
            )
        ],
        lastActivity="2026-05-16",
        projectPath="-Users-q",
    )


def test_session_token_mix_has_four_bars() -> None:
    fig = session_token_mix(_session())
    assert isinstance(fig, go.Figure)
    kinds = {t.name for t in fig.data}
    assert kinds == {"input", "output", "cache_create", "cache_read"}


# ---------- burn_gauge ----------


def _block_with_burn() -> BlockEntry:
    return BlockEntry(
        id="2026-05-16T13:00:00.000Z",
        startTime="2026-05-16T13:00:00.000Z",
        endTime="2026-05-16T18:00:00.000Z",
        actualEndTime=None,
        isActive=True,
        isGap=False,
        entries=1,
        tokenCounts=BlockTokenCounts(
            inputTokens=10, outputTokens=20, cacheCreationInputTokens=30, cacheReadInputTokens=40
        ),
        totalTokens=100,
        costUSD=1.0,
        models=["claude-opus-4-7"],
        burnRate=BurnRate(
            tokensPerMinute=1.0,
            tokensPerMinuteForIndicator=1.0,
            costPerHour=8.83,
        ),
        projection=Projection(totalTokens=999, totalCost=38.48, remainingMinutes=259),
    )


def test_burn_gauge_returns_indicator_figure() -> None:
    fig = burn_gauge(_block_with_burn())
    assert isinstance(fig, go.Figure)
    # Single Indicator trace.
    assert len(fig.data) == 1
    assert fig.data[0].value == 8.83


def test_burn_gauge_with_typical_renders_threshold_marker() -> None:
    """When ``typical`` is provided and > 0, the gauge gets a red
    threshold line at that value — visual cue for "above/below my usual
    burn". Threshold structure: gauge.threshold = {"line": {"color":
    "#d62728", ...}, "thickness": 0.85, "value": typical}."""
    fig = burn_gauge(_block_with_burn(), typical=5.0)
    assert isinstance(fig, go.Figure)
    threshold = fig.data[0].gauge.threshold
    assert threshold is not None
    assert threshold.value == 5.0
    assert threshold.line.color == "#d62728"


def test_burn_gauge_typical_zero_does_not_set_threshold() -> None:
    """A typical of 0 (or negative) is meaningless as a threshold — the
    burn-rate axis starts at 0. Guard ``typical > 0`` so a defaulted-to-
    zero historical-median doesn't paint a phantom red line at the axis."""
    fig = burn_gauge(_block_with_burn(), typical=0.0)
    assert isinstance(fig, go.Figure)
    threshold = fig.data[0].gauge.threshold
    # Plotly returns an unset threshold as a degenerate object whose
    # `value` is None — that's the "no threshold drawn" shape.
    assert threshold is None or threshold.value is None


def test_burn_gauge_no_burn_rate_returns_none() -> None:
    block = _block_with_burn()
    block = BlockEntry(
        id=block.id,
        startTime=block.start_time,
        endTime=block.end_time,
        actualEndTime=block.actual_end_time,
        isActive=block.is_active,
        isGap=block.is_gap,
        entries=block.entries,
        tokenCounts=block.token_counts,
        totalTokens=block.total_tokens,
        costUSD=block.cost_usd,
        models=block.models,
        burnRate=None,
        projection=None,
    )
    assert burn_gauge(block) is None


# ---------- cache_hit_ratio_line ----------


def test_cache_hit_ratio_line_returns_figure() -> None:
    report = _report(
        [
            _entry("2026-05-15", cost=1.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=2.0, model="claude-opus-4-7"),
        ]
    )
    fig = cache_hit_ratio_line(report)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    # 2 points, ratio in [0,1].
    assert len(fig.data[0].x) == 2
    assert all(0.0 <= r <= 1.0 for r in fig.data[0].y)


def test_cache_hit_ratio_line_empty_returns_none() -> None:
    assert cache_hit_ratio_line(_report([])) is None


# ---------- token_flow_sankey ----------


def test_token_flow_sankey_returns_figure() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_flow_sankey(report)
    assert isinstance(fig, go.Figure)
    # Single Sankey trace.
    assert len(fig.data) == 1
    trace = fig.data[0]
    # 4 token kinds + 1 family = 5 nodes.
    assert len(trace.node.label) == 5


def test_token_flow_sankey_empty_returns_none() -> None:
    assert token_flow_sankey(_report([])) is None


# ---------- single_family_token_bar ----------


def test_single_family_token_bar_returns_horizontal_log_bar() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = single_family_token_bar(report)
    assert isinstance(fig, go.Figure)
    # Coloured by kind → one trace per category (4 total) so each bar gets
    # a distinct colour from Plotly's qualitative palette.
    assert len(fig.data) == 4
    trace_names = {t.name for t in fig.data}
    assert trace_names == {"cache_read", "cache_create", "output", "input"}
    # Log x so cache_read doesn't drown input.
    assert fig.layout.xaxis.type == "log"
    # Distinct colours: every trace has a different marker color.
    colors = {t.marker.color for t in fig.data if t.marker.color is not None}
    assert len(colors) == 4


def test_single_family_token_bar_empty_returns_none() -> None:
    assert single_family_token_bar(_report([])) is None


def test_single_family_token_bar_all_zero_tokens_returns_none() -> None:
    """A report with entries but every token count == 0 (e.g. cost-only
    accounting glitches) would render four zero-width bars. Bail and
    let the UI render an empty-state caption instead."""
    zero_entry = DailyEntry(
        date="2026-05-16",
        inputTokens=0,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=0,
        totalTokens=0,
        totalCost=0.0,
        modelsUsed=["claude-opus-4-7"],
        modelBreakdowns=[
            ModelBreakdown(
                modelName="claude-opus-4-7",
                inputTokens=0,
                outputTokens=0,
                cacheCreationTokens=0,
                cacheReadTokens=0,
                cost=0.0,
            )
        ],
    )
    assert single_family_token_bar(_report([zero_entry])) is None


# ---------- session_blocks_timeline (slice 17) ----------


def _block(*, block_id: str, start: str, end: str, is_active: bool = False) -> BlockEntry:
    return BlockEntry(
        id=block_id,
        startTime=start,
        endTime=end,
        actualEndTime=None,
        isActive=is_active,
        isGap=False,
        entries=1,
        tokenCounts=BlockTokenCounts(
            inputTokens=10, outputTokens=20,
            cacheCreationInputTokens=30, cacheReadInputTokens=40,
        ),
        totalTokens=100,
        costUSD=1.0,
        models=["claude-opus-4-7"],
        burnRate=None,
        projection=None,
    )


def test_session_blocks_timeline_renders_with_blocks() -> None:
    blocks = [
        _block(block_id="b-1", start="2026-05-16T13:00:00.000Z", end="2026-05-16T18:00:00.000Z"),
        _block(block_id="b-2", start="2026-05-16T19:00:00.000Z", end="2026-05-17T00:00:00.000Z", is_active=True),
    ]
    fig = session_blocks_timeline(blocks)
    assert isinstance(fig, go.Figure)
    # px.timeline emits one trace per colour category (Active/Completed).
    trace_names = {t.name for t in fig.data}
    assert "Active" in trace_names and "Completed" in trace_names


def test_session_blocks_timeline_empty_returns_none() -> None:
    assert session_blocks_timeline([]) is None


def test_session_blocks_timeline_tz_label() -> None:
    blocks = [
        _block(block_id="b-1", start="2026-05-16T13:00:00.000Z", end="2026-05-16T18:00:00.000Z"),
    ]
    fig = session_blocks_timeline(blocks, tz="America/Los_Angeles")
    # The x-axis title shows the user's zone so they're not guessing UTC.
    assert "Los_Angeles" in (fig.layout.xaxis.title.text or "")
