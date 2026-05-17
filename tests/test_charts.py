"""Tests for tokenscope.ui.charts — Plotly figure builders.

These tests don't require a Streamlit runtime; the builders return
plotly.graph_objects.Figure objects we can inspect directly. The goal is
not to pin down Plotly internals — just to confirm that the data we
shaped flows through and that empty inputs short-circuit cleanly.
"""

from __future__ import annotations

import plotly.graph_objects as go
import pytest

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
    BRAND_HUE_SHADES,
    _daily_metric_figure,
    apply_enterprise_style,
    burn_gauge,
    cache_hit_ratio_line,
    cost_trend_with_rolling,
    donut_cost_by_model,
    session_blocks_timeline,
    session_token_mix,
    single_family_token_bar,
    token_flow_sankey,
    token_mix_percent_bar,
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


# ---------- cost_trend_with_rolling ----------


def test_cost_trend_returns_figure_with_family_traces() -> None:
    report = _report(
        [
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=1.0, model="claude-haiku-4-5-20251001"),
            _entry("2026-05-16", cost=4.0, model="claude-opus-4-7"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    assert isinstance(fig, go.Figure)
    # Both family bands + the rolling-average overlay are present.
    trace_names = {t.name for t in fig.data}
    assert "opus" in trace_names and "haiku" in trace_names
    assert "7-day avg" in trace_names


def test_cost_trend_empty_returns_none() -> None:
    assert cost_trend_with_rolling(_report([])) is None


def test_cost_trend_single_day_uses_bar_not_area() -> None:
    """Single-day windows fall back to a stacked bar — px.area paints
    zero width with one x-value and the chart looks empty."""
    report = _report(
        [
            _entry("2026-05-16", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=1.0, model="claude-haiku-4-5-20251001"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    assert isinstance(fig, go.Figure)
    assert all(t.type == "bar" for t in fig.data), (
        f"expected bar traces on single-day window; got {[t.type for t in fig.data]}"
    )
    assert fig.layout.barmode == "stack"


def test_cost_trend_brand_hue_palette_used() -> None:
    """Family bands draw from the brand-hue shade sequence so the chart
    stays in family-tonal territory rather than picking from Plotly's
    default qualitative palette (which includes red)."""
    report = _report(
        [
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=4.0, model="claude-opus-4-7"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    band = next(t for t in fig.data if t.name == "opus")
    # The first family's fill color should match the first brand-hue shade.
    fill = band.fillcolor if hasattr(band, "fillcolor") else None
    line = band.line.color if hasattr(band, "line") and band.line else None
    assert (fill in BRAND_HUE_SHADES) or (line in BRAND_HUE_SHADES), (
        f"expected brand-hue color on `opus` band; got fill={fill!r} line={line!r}"
    )


def test_cost_trend_adds_spike_annotation_when_provided() -> None:
    """`spike=(date, cost)` adds a Plotly annotation calling out the
    outlier day. Without the kwarg, no annotation is drawn."""
    report = _report(
        [
            _entry("2026-04-18", cost=400.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=4.0, model="claude-opus-4-7"),
        ]
    )
    annotated = cost_trend_with_rolling(report, spike=("2026-04-18", 400.0))
    plain = cost_trend_with_rolling(report)
    assert annotated is not None and plain is not None
    annotated_texts = [a.text for a in annotated.layout.annotations or []]
    plain_texts = [a.text for a in plain.layout.annotations or []]
    assert any(
        "2026-04-18" in t and "400" in t for t in annotated_texts
    ), f"expected spike annotation; got: {annotated_texts!r}"
    assert plain_texts == [], (
        f"expected no annotations without spike kwarg; got: {plain_texts!r}"
    )


# ---------- token_mix_percent_bar ----------


def test_token_mix_percent_returns_figure_with_four_kinds() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_percent_bar(report)
    assert isinstance(fig, go.Figure)
    kinds = {t.name for t in fig.data}
    assert kinds == {"input", "output", "cache_create", "cache_read"}


def test_token_mix_percent_each_day_sums_to_one_hundred() -> None:
    """The chart answers a composition question — each day's bars
    must sum to 100% so the user reads the mix directly."""
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_percent_bar(report)
    # Sum the y-values across all traces for the single day.
    total = sum(float(t.y[0]) for t in fig.data)
    assert total == pytest.approx(100.0, abs=0.01)


def test_token_mix_percent_uses_stack_not_overlay() -> None:
    """Percent-stacked = barmode='stack'. Overlay would have all four
    kinds painting from 0 to their own percent, defeating the mix
    visualisation."""
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_percent_bar(report)
    assert fig.layout.barmode == "stack"
    # Y-axis range is locked to [0, 100] so the percent-stacks read
    # consistently across days.
    assert fig.layout.yaxis.range is not None
    assert list(fig.layout.yaxis.range) == [0, 100]


def test_token_mix_percent_empty_returns_none() -> None:
    assert token_mix_percent_bar(_report([])) is None


def test_token_mix_percent_zero_day_has_zero_percents() -> None:
    """A zero-token day mustn't trigger a divide-by-zero — its
    percents are all 0 (no bar visible at that x)."""
    zero_entry = DailyEntry(
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
    report = DailyReport(
        daily=[zero_entry],
        totals=Totals(
            inputTokens=0,
            outputTokens=0,
            cacheCreationTokens=0,
            cacheReadTokens=0,
            totalTokens=0,
            totalCost=0.0,
        ),
    )
    fig = token_mix_percent_bar(report)
    # Figure should still build (no NaN crash), just with zero-height bars.
    assert isinstance(fig, go.Figure)
    for trace in fig.data:
        assert all(y == 0 for y in trace.y)


# ---------- apply_enterprise_style ----------


def test_apply_enterprise_style_clears_title_and_axis_titles() -> None:
    """The enterprise style drops any in-chart title and axis titles —
    the section H3 above the chart owns the title, the ticks own the
    axes."""
    fig = go.Figure()
    fig.update_layout(
        title="Should be removed",
        xaxis_title="Date",
        yaxis_title="Cost (USD)",
    )
    styled = apply_enterprise_style(fig)
    assert styled.layout.title.text is None
    assert styled.layout.xaxis.title.text is None
    assert styled.layout.yaxis.title.text is None


def test_apply_enterprise_style_horizontal_gridlines_only() -> None:
    """Horizontal dotted gridlines only — no vertical, no axis spines."""
    fig = apply_enterprise_style(go.Figure())
    assert fig.layout.xaxis.showgrid is False
    assert fig.layout.yaxis.showgrid is True
    assert fig.layout.xaxis.zeroline is False
    assert fig.layout.yaxis.zeroline is False
    assert fig.layout.xaxis.showline is False
    assert fig.layout.yaxis.showline is False


def test_apply_enterprise_style_is_idempotent() -> None:
    """Applying the style twice produces the same layout as applying
    it once. Builders compose freely."""
    fig = go.Figure()
    once = apply_enterprise_style(fig).to_dict()["layout"]
    twice = apply_enterprise_style(apply_enterprise_style(go.Figure())).to_dict()["layout"]
    assert once == twice


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
