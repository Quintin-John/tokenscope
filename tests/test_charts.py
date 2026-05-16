"""Tests for tokenscope.ui.charts — Plotly figure builders.

These tests don't require a Streamlit runtime; the builders return
plotly.graph_objects.Figure objects we can inspect directly. The goal is
not to pin down Plotly internals — just to confirm that the data we
shaped flows through and that empty inputs short-circuit cleanly.
"""

from __future__ import annotations

import plotly.graph_objects as go

from tokenscope.models import DailyEntry, DailyReport, ModelBreakdown, Totals
from tokenscope.ui.charts import (
    rolling_average_line,
    stacked_area_cost_by_family,
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


def test_token_mix_bar_empty_returns_none() -> None:
    assert token_mix_bar(_report([])) is None
