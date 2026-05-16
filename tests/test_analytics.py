"""Unit tests for tokenscope.analytics — pure functions, synthetic data.

These tests don't touch ccusage, the filesystem, or Streamlit. They
exercise edge cases (zero tokens, single-model, all-cache, all-input,
empty inputs, non-positive windows) so future refactors of the rollups
will get caught immediately.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tokenscope.analytics import (
    cache_hit_ratio,
    dollars_saved,
    model_family,
    rolling_cost_average,
    top_n_by_cost,
)
from tokenscope.models import (
    DailyEntry,
    DailyReport,
    ModelBreakdown,
    Totals,
)


def _breakdown(name: str, cost: float = 1.0) -> ModelBreakdown:
    return ModelBreakdown(
        modelName=name,
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        cost=cost,
    )


def _entry(
    date: str,
    *,
    input_tokens: int = 100,
    output_tokens: int = 200,
    cache_creation_tokens: int = 300,
    cache_read_tokens: int = 400,
    total_cost: float = 1.0,
    models: list[str] | None = None,
) -> DailyEntry:
    used = models or ["claude-opus-4-7"]
    return DailyEntry(
        date=date,
        inputTokens=input_tokens,
        outputTokens=output_tokens,
        cacheCreationTokens=cache_creation_tokens,
        cacheReadTokens=cache_read_tokens,
        totalTokens=input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens,
        totalCost=total_cost,
        modelsUsed=used,
        modelBreakdowns=[_breakdown(used[0], cost=total_cost)],
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


# ---------- rolling_cost_average ----------


def test_rolling_cost_average_three_day_window() -> None:
    report = _report(
        [
            _entry("2026-04-01", total_cost=1.0),
            _entry("2026-04-02", total_cost=2.0),
            _entry("2026-04-03", total_cost=3.0),
            _entry("2026-04-04", total_cost=4.0),
            _entry("2026-04-05", total_cost=5.0),
        ]
    )
    result = rolling_cost_average(report, window_days=3)
    dates = [d for d, _ in result]
    means = [m for _, m in result]
    assert dates == ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04", "2026-04-05"]
    assert means[0] == pytest.approx(1.0)
    assert means[1] == pytest.approx(1.5)
    assert means[2] == pytest.approx(2.0)
    assert means[3] == pytest.approx(3.0)
    assert means[4] == pytest.approx(4.0)


def test_rolling_cost_average_window_larger_than_data() -> None:
    report = _report(
        [_entry("2026-04-01", total_cost=1.0), _entry("2026-04-02", total_cost=3.0)]
    )
    result = rolling_cost_average(report, window_days=30)
    assert [d for d, _ in result] == ["2026-04-01", "2026-04-02"]
    assert result[0][1] == pytest.approx(1.0)
    assert result[1][1] == pytest.approx(2.0)


def test_rolling_cost_average_window_one_returns_per_day_cost() -> None:
    report = _report(
        [_entry("2026-04-01", total_cost=7.5), _entry("2026-04-02", total_cost=2.25)]
    )
    result = rolling_cost_average(report, window_days=1)
    assert result == [("2026-04-01", 7.5), ("2026-04-02", 2.25)]


def test_rolling_cost_average_sorts_unordered_input() -> None:
    report = _report(
        [
            _entry("2026-04-03", total_cost=3.0),
            _entry("2026-04-01", total_cost=1.0),
            _entry("2026-04-02", total_cost=2.0),
        ]
    )
    result = rolling_cost_average(report, window_days=2)
    assert [d for d, _ in result] == ["2026-04-01", "2026-04-02", "2026-04-03"]
    assert result[1][1] == pytest.approx(1.5)
    assert result[2][1] == pytest.approx(2.5)


def test_rolling_cost_average_empty_report() -> None:
    report = _report([])
    assert rolling_cost_average(report, window_days=7) == []


def test_rolling_cost_average_invalid_window_raises() -> None:
    report = _report([_entry("2026-04-01")])
    with pytest.raises(ValueError, match="window_days must be >= 1"):
        rolling_cost_average(report, window_days=0)
    with pytest.raises(ValueError, match="window_days must be >= 1"):
        rolling_cost_average(report, window_days=-3)


# ---------- cache_hit_ratio ----------


def test_cache_hit_ratio_typical() -> None:
    entry = _entry(
        "2026-04-01",
        input_tokens=10,
        cache_creation_tokens=20,
        cache_read_tokens=70,
    )
    # 70 / (10 + 20 + 70) = 0.7
    assert cache_hit_ratio(entry) == pytest.approx(0.7)


def test_cache_hit_ratio_all_cache_read() -> None:
    entry = _entry(
        "2026-04-01",
        input_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=1000,
    )
    assert cache_hit_ratio(entry) == pytest.approx(1.0)


def test_cache_hit_ratio_all_input_no_cache() -> None:
    entry = _entry(
        "2026-04-01",
        input_tokens=1000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cache_hit_ratio(entry) == 0.0


def test_cache_hit_ratio_zero_tokens() -> None:
    entry = _entry(
        "2026-04-01",
        input_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
    )
    assert cache_hit_ratio(entry) == 0.0


def test_cache_hit_ratio_excludes_output_tokens() -> None:
    """Output tokens should not affect the ratio (denominator excludes them)."""
    base = _entry(
        "2026-04-01",
        input_tokens=10,
        cache_creation_tokens=10,
        cache_read_tokens=80,
        output_tokens=0,
    )
    huge_output = _entry(
        "2026-04-01",
        input_tokens=10,
        cache_creation_tokens=10,
        cache_read_tokens=80,
        output_tokens=10_000_000,
    )
    assert cache_hit_ratio(base) == cache_hit_ratio(huge_output)


# ---------- dollars_saved ----------


def test_dollars_saved_typical() -> None:
    entry = _entry("2026-04-01", cache_read_tokens=2_000_000)
    # 2M tokens * $3/MTok = $6.00
    assert dollars_saved(entry, input_price_per_mtok=3.0) == pytest.approx(6.0)


def test_dollars_saved_zero_cache_reads() -> None:
    entry = _entry("2026-04-01", cache_read_tokens=0)
    assert dollars_saved(entry, input_price_per_mtok=15.0) == 0.0


def test_dollars_saved_zero_price() -> None:
    entry = _entry("2026-04-01", cache_read_tokens=1_000_000)
    assert dollars_saved(entry, input_price_per_mtok=0.0) == 0.0


def test_dollars_saved_negative_price_treated_as_undefined() -> None:
    entry = _entry("2026-04-01", cache_read_tokens=1_000_000)
    assert dollars_saved(entry, input_price_per_mtok=-1.0) == 0.0


def test_dollars_saved_fractional_million() -> None:
    entry = _entry("2026-04-01", cache_read_tokens=250_000)
    # 0.25M * $15 = $3.75
    assert dollars_saved(entry, input_price_per_mtok=15.0) == pytest.approx(3.75)


# ---------- top_n_by_cost ----------


def test_top_n_by_cost_picks_highest() -> None:
    entries = [
        _entry("2026-04-01", total_cost=1.0),
        _entry("2026-04-02", total_cost=9.0),
        _entry("2026-04-03", total_cost=3.0),
        _entry("2026-04-04", total_cost=7.0),
    ]
    top = top_n_by_cost(entries, n=2)
    assert [e.date for e in top] == ["2026-04-02", "2026-04-04"]


def test_top_n_by_cost_uses_cost_attr_on_breakdowns() -> None:
    breakdowns = [
        _breakdown("claude-opus-4-7", cost=2.0),
        _breakdown("claude-haiku-4-5", cost=10.0),
        _breakdown("claude-sonnet-4-6", cost=5.0),
    ]
    top = top_n_by_cost(breakdowns, n=2)
    assert [b.model_name for b in top] == ["claude-haiku-4-5", "claude-sonnet-4-6"]


def test_top_n_by_cost_falls_back_to_cost_usd() -> None:
    @dataclass
    class BlockLike:
        id: str
        cost_usd: float

    entries = [BlockLike(id="a", cost_usd=1.0), BlockLike(id="b", cost_usd=4.0)]
    assert [e.id for e in top_n_by_cost(entries, n=1)] == ["b"]


def test_top_n_by_cost_n_zero_returns_empty() -> None:
    entries = [_entry("2026-04-01", total_cost=5.0)]
    assert top_n_by_cost(entries, n=0) == []


def test_top_n_by_cost_n_negative_returns_empty() -> None:
    entries = [_entry("2026-04-01", total_cost=5.0)]
    assert top_n_by_cost(entries, n=-3) == []


def test_top_n_by_cost_n_larger_than_input() -> None:
    entries = [
        _entry("2026-04-01", total_cost=1.0),
        _entry("2026-04-02", total_cost=2.0),
    ]
    top = top_n_by_cost(entries, n=10)
    assert [e.date for e in top] == ["2026-04-02", "2026-04-01"]


def test_top_n_by_cost_missing_cost_attrs_treated_as_zero() -> None:
    @dataclass
    class NoCost:
        label: str

    entries = [NoCost("a"), _entry("2026-04-01", total_cost=1.0), NoCost("b")]
    top = top_n_by_cost(entries, n=3)
    # The DailyEntry with cost 1.0 wins; the two NoCost items both score 0.
    assert getattr(top[0], "date", None) == "2026-04-01"


def test_top_n_by_cost_empty_iterable() -> None:
    assert top_n_by_cost([], n=5) == []


# ---------- model_family ----------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("claude-opus-4-7", "opus"),
        ("claude-opus-4-6", "opus"),
        ("claude-haiku-4-5-20251001", "haiku"),
        ("claude-sonnet-4-6", "sonnet"),
        ("claude-3-5-sonnet-20240620", "sonnet"),
        ("gpt-4o", "gpt-4o"),
        ("", ""),
        ("claude", "claude"),
    ],
)
def test_model_family(name: str, expected: str) -> None:
    assert model_family(name) == expected
