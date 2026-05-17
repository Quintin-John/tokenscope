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
