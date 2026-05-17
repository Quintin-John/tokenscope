"""Unit tests for tokenscope.analytics — pure functions, synthetic data.

These tests don't touch ccusage, the filesystem, or Streamlit. They
exercise edge cases (zero tokens, single-model, all-cache, all-input,
empty inputs, non-positive windows) so future refactors of the rollups
will get caught immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from tokenscope.analytics import (
    active_block_burn,
    aggregate_cache_hit_ratio,
    available_models,
    blocks_for_session,
    blocks_on_day,
    cache_hit_ratio,
    cost_by_kind,
    cost_share_by_model,
    daily_cache_hit_ratio,
    daily_cost_by_model,
    daily_token_mix,
    bold_numbers_in_insight,
    collapse_composition_rows,
    filter_daily_by_models,
    find_block,
    find_daily_entry,
    find_session,
    format_compact_int,
    format_timezone_for_display,
    friendly_project_label,
    last_day_cost,
    overview_insight,
    spike_day,
    model_breakdown,
    model_family,
    mtd_cost,
    prior_window_query,
    rolling_cost_average,
    sessions_on_day,
    short_model_label,
    today_cost,
    token_flow_sankey_data,
    top_n_by_cost,
    typical_burn_rate,
    window_cost,
    window_effective_per_mtok,
)
from tokenscope.query import Query
from tokenscope.models import (
    BlockEntry,
    BlocksReport,
    BlockTokenCounts,
    BurnRate,
    DailyEntry,
    DailyReport,
    ModelBreakdown,
    SessionEntry,
    SessionReport,
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
    model_breakdowns: list[ModelBreakdown] | None = None,
) -> DailyEntry:
    used = models or ["claude-opus-4-7"]
    breakdowns = model_breakdowns if model_breakdowns is not None else [_breakdown(used[0], cost=total_cost)]
    return DailyEntry(
        date=date,
        inputTokens=input_tokens,
        outputTokens=output_tokens,
        cacheCreationTokens=cache_creation_tokens,
        cacheReadTokens=cache_read_tokens,
        totalTokens=input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens,
        totalCost=total_cost,
        modelsUsed=used,
        modelBreakdowns=breakdowns,
    )


def _block(
    *,
    block_id: str = "2026-05-16T13:00:00.000Z",
    start_time: str = "2026-05-16T13:00:00.000Z",
    end_time: str = "2026-05-16T18:00:00.000Z",
    is_active: bool = True,
    is_gap: bool = False,
    cost_per_hour: float | None = 5.0,
    cost_usd: float = 1.0,
) -> BlockEntry:
    return BlockEntry(
        id=block_id,
        startTime=start_time,
        endTime=end_time,
        actualEndTime=None,
        isActive=is_active,
        isGap=is_gap,
        entries=1,
        tokenCounts=BlockTokenCounts(
            inputTokens=10,
            outputTokens=20,
            cacheCreationInputTokens=30,
            cacheReadInputTokens=40,
        ),
        totalTokens=100,
        costUSD=cost_usd,
        models=["claude-opus-4-7"],
        burnRate=BurnRate(
            tokensPerMinute=1.0,
            tokensPerMinuteForIndicator=1.0,
            costPerHour=cost_per_hour,
        )
        if cost_per_hour is not None
        else None,
        projection=None,
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
        # Defensive: falsy model names map to `"other"`, never the
        # empty string. Plotly's JS layer stringifies empty category
        # names to `"undefined"` in legends, which previously surfaced
        # as a phantom legend entry on the Overview Daily-cost chart.
        ("", "other"),
        ("claude", "claude"),
    ],
)
def test_model_family(name: str, expected: str) -> None:
    assert model_family(name) == expected


def test_model_family_falsy_inputs_return_fallback() -> None:
    """Regression for the `undefined` legend bug: any falsy model
    name returns the documented fallback sentinel, not the input."""
    from tokenscope.analytics import UNKNOWN_MODEL_FAMILY

    assert model_family("") == UNKNOWN_MODEL_FAMILY
    assert UNKNOWN_MODEL_FAMILY == "other"


# ---------- mtd_cost ----------


def test_mtd_cost_sums_current_month_only() -> None:
    report = _report(
        [
            _entry("2026-04-28", total_cost=10.0),
            _entry("2026-04-30", total_cost=5.0),
            _entry("2026-05-01", total_cost=1.0),
            _entry("2026-05-15", total_cost=2.5),
            _entry("2026-06-01", total_cost=99.0),
        ]
    )
    assert mtd_cost(report, today=date(2026, 5, 16)) == pytest.approx(3.5)


def test_mtd_cost_no_entries_in_month() -> None:
    report = _report([_entry("2026-04-01", total_cost=10.0)])
    assert mtd_cost(report, today=date(2026, 5, 16)) == 0.0


def test_mtd_cost_empty_report() -> None:
    report = _report([])
    assert mtd_cost(report, today=date(2026, 5, 16)) == 0.0


# ---------- today_cost ----------


def test_today_cost_match() -> None:
    report = _report(
        [
            _entry("2026-05-15", total_cost=1.0),
            _entry("2026-05-16", total_cost=2.5),
            _entry("2026-05-17", total_cost=3.0),
        ]
    )
    assert today_cost(report, today=date(2026, 5, 16)) == pytest.approx(2.5)


def test_today_cost_no_entry_for_today() -> None:
    report = _report([_entry("2026-05-15", total_cost=1.0)])
    assert today_cost(report, today=date(2026, 5, 16)) == 0.0


def test_today_cost_empty_report() -> None:
    assert today_cost(_report([]), today=date(2026, 5, 16)) == 0.0


# ---------- window_cost ----------


def test_window_cost_sums_across_window() -> None:
    """Regression for the 'KPIs should reflect the picked range' fix."""
    report = _report(
        [
            _entry("2026-04-01", total_cost=10.0),
            _entry("2026-05-01", total_cost=1.0),
            _entry("2026-05-16", total_cost=2.5),
        ]
    )
    assert window_cost(report) == pytest.approx(13.5)


def test_window_cost_empty_report() -> None:
    assert window_cost(_report([])) == 0.0


# ---------- last_day_cost ----------


def test_last_day_cost_picks_max_date() -> None:
    report = _report(
        [
            _entry("2026-05-14", total_cost=70.11),
            _entry("2026-05-16", total_cost=39.63),
            _entry("2026-05-12", total_cost=35.88),
        ]
    )
    result = last_day_cost(report)
    assert result == ("2026-05-16", pytest.approx(39.63))


def test_last_day_cost_empty_report() -> None:
    assert last_day_cost(_report([])) is None


def test_last_day_cost_does_not_depend_on_system_today() -> None:
    """Window may end in the past — KPI should still pick the latest in-window day."""
    report = _report(
        [
            _entry("2025-12-30", total_cost=1.0),
            _entry("2025-12-31", total_cost=2.0),
        ]
    )
    assert last_day_cost(report) == ("2025-12-31", pytest.approx(2.0))


# ---------- aggregate_cache_hit_ratio ----------


def test_aggregate_cache_hit_ratio_typical() -> None:
    report = _report(
        [
            _entry("2026-05-15", input_tokens=10, cache_creation_tokens=10, cache_read_tokens=80),
            _entry("2026-05-16", input_tokens=20, cache_creation_tokens=0, cache_read_tokens=80),
        ]
    )
    # (80 + 80) / (10+10+80 + 20+0+80) = 160 / 200 = 0.8
    assert aggregate_cache_hit_ratio(report) == pytest.approx(0.8)


def test_aggregate_cache_hit_ratio_weighted_by_volume() -> None:
    """A 100%-cache big day should dominate a 0%-cache small day."""
    report = _report(
        [
            _entry("2026-05-15", input_tokens=1, cache_creation_tokens=0, cache_read_tokens=0),
            _entry("2026-05-16", input_tokens=0, cache_creation_tokens=0, cache_read_tokens=1_000_000),
        ]
    )
    ratio = aggregate_cache_hit_ratio(report)
    assert ratio > 0.999  # heavy second day pulls it up


def test_aggregate_cache_hit_ratio_zero_tokens() -> None:
    report = _report(
        [_entry("2026-05-16", input_tokens=0, cache_creation_tokens=0, cache_read_tokens=0, output_tokens=0)]
    )
    assert aggregate_cache_hit_ratio(report) == 0.0


def test_aggregate_cache_hit_ratio_empty_report() -> None:
    assert aggregate_cache_hit_ratio(_report([])) == 0.0


# ---------- active_block_burn ----------


def test_active_block_burn_returns_cost_per_hour() -> None:
    report = BlocksReport(blocks=[_block(is_active=True, cost_per_hour=8.83)])
    assert active_block_burn(report) == pytest.approx(8.83)


def test_active_block_burn_no_active_block() -> None:
    report = BlocksReport(blocks=[_block(is_active=False, cost_per_hour=8.83)])
    assert active_block_burn(report) is None


def test_active_block_burn_active_with_no_burn_rate() -> None:
    report = BlocksReport(blocks=[_block(is_active=True, cost_per_hour=None)])
    assert active_block_burn(report) is None


def test_active_block_burn_empty_blocks() -> None:
    assert active_block_burn(BlocksReport(blocks=[])) is None


# ---------- daily_cost_by_model ----------


def test_daily_cost_by_model_flattens_entries() -> None:
    breakdowns = [
        _breakdown("claude-opus-4-7", cost=10.0),
        _breakdown("claude-haiku-4-5-20251001", cost=1.0),
    ]
    report = _report(
        [
            _entry(
                "2026-05-16",
                total_cost=11.0,
                models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
                model_breakdowns=breakdowns,
            )
        ]
    )
    rows = daily_cost_by_model(report)
    assert rows == [
        {"date": "2026-05-16", "model": "claude-opus-4-7", "family": "opus", "cost": 10.0},
        {"date": "2026-05-16", "model": "claude-haiku-4-5-20251001", "family": "haiku", "cost": 1.0},
    ]


def test_daily_cost_by_model_empty_report() -> None:
    assert daily_cost_by_model(_report([])) == []


# ---------- daily_token_mix ----------


def test_daily_token_mix_emits_four_rows_per_day() -> None:
    report = _report(
        [
            _entry(
                "2026-05-16",
                input_tokens=1,
                output_tokens=2,
                cache_creation_tokens=3,
                cache_read_tokens=4,
            )
        ]
    )
    rows = daily_token_mix(report)
    assert rows == [
        {"date": "2026-05-16", "kind": "input", "tokens": 1},
        {"date": "2026-05-16", "kind": "output", "tokens": 2},
        {"date": "2026-05-16", "kind": "cache_create", "tokens": 3},
        {"date": "2026-05-16", "kind": "cache_read", "tokens": 4},
    ]


def test_daily_token_mix_empty_report() -> None:
    assert daily_token_mix(_report([])) == []


# ---------- find_daily_entry ----------


def test_find_daily_entry_hit() -> None:
    report = _report([_entry("2026-05-15"), _entry("2026-05-16", total_cost=9.0)])
    e = find_daily_entry(report, "2026-05-16")
    assert e is not None and e.total_cost == 9.0


def test_find_daily_entry_miss() -> None:
    assert find_daily_entry(_report([_entry("2026-05-15")]), "2026-05-16") is None


# ---------- sessions_on_day / find_session ----------


def _session(session_id: str, last_activity: str, *, cost: float = 1.0) -> SessionEntry:
    return SessionEntry(
        sessionId=session_id,
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        totalTokens=100,
        totalCost=cost,
        modelsUsed=["claude-opus-4-7"],
        modelBreakdowns=[_breakdown("claude-opus-4-7", cost=cost)],
        lastActivity=last_activity,
        projectPath="-Users-q-proj",
    )


def _session_report(sessions: list[SessionEntry]) -> SessionReport:
    return SessionReport(
        sessions=sessions,
        totals=Totals(
            inputTokens=sum(s.input_tokens for s in sessions),
            outputTokens=sum(s.output_tokens for s in sessions),
            cacheCreationTokens=sum(s.cache_creation_tokens for s in sessions),
            cacheReadTokens=sum(s.cache_read_tokens for s in sessions),
            totalTokens=sum(s.total_tokens for s in sessions),
            totalCost=sum(s.total_cost for s in sessions),
        ),
    )


def test_sessions_on_day_filters_by_last_activity() -> None:
    rep = _session_report(
        [
            _session("a", "2026-05-15"),
            _session("b", "2026-05-16"),
            _session("c", "2026-05-16"),
        ]
    )
    matched = sessions_on_day(rep, "2026-05-16")
    assert [s.session_id for s in matched] == ["b", "c"]


def test_sessions_on_day_empty() -> None:
    assert sessions_on_day(_session_report([]), "2026-05-16") == []


def test_find_session_hit_and_miss() -> None:
    rep = _session_report([_session("a", "2026-05-16", cost=4.0)])
    assert find_session(rep, "a").total_cost == 4.0
    assert find_session(rep, "missing") is None


# ---------- blocks_on_day / find_block ----------


def test_blocks_on_day_filters_by_start_time_prefix() -> None:
    rep = BlocksReport(
        blocks=[
            _block(block_id="b1", start_time="2026-05-16T13:00:00.000Z", is_active=False),
            _block(block_id="b2", start_time="2026-05-15T13:00:00.000Z", is_active=False),
        ]
    )
    matched = blocks_on_day(rep, "2026-05-16")
    assert [b.id for b in matched] == ["b1"]


def test_blocks_on_day_excludes_gap_blocks() -> None:
    gap = _block(block_id="gap-1", start_time="2026-05-16T08:00:00.000Z", is_gap=True)
    assert blocks_on_day(BlocksReport(blocks=[gap]), "2026-05-16") == []


def test_find_block_hit_and_miss() -> None:
    b = _block(is_active=True, cost_per_hour=8.83)
    rep = BlocksReport(blocks=[b])
    assert find_block(rep, b.id) is b
    assert find_block(rep, "nope") is None


# ---------- cost_share_by_model ----------


def test_cost_share_by_model_for_daily_entry() -> None:
    breakdowns = [
        _breakdown("claude-opus-4-7", cost=10.0),
        _breakdown("claude-haiku-4-5-20251001", cost=1.0),
    ]
    entry = _entry(
        "2026-05-16",
        total_cost=11.0,
        models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
        model_breakdowns=breakdowns,
    )
    rows = cost_share_by_model(entry)
    assert rows == [
        {"model": "claude-opus-4-7", "family": "opus", "cost": 10.0},
        {"model": "claude-haiku-4-5-20251001", "family": "haiku", "cost": 1.0},
    ]


# ---------- filter_daily_by_models ----------


def test_filter_daily_by_models_recomputes_totals() -> None:
    breakdowns = [
        _breakdown("claude-opus-4-7", cost=10.0),
        _breakdown("claude-haiku-4-5-20251001", cost=1.0),
    ]
    report = _report(
        [
            _entry(
                "2026-05-16",
                total_cost=11.0,
                models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
                model_breakdowns=breakdowns,
            )
        ]
    )
    filtered = filter_daily_by_models(report, ["claude-opus-4-7"])
    assert len(filtered.daily) == 1
    only = filtered.daily[0]
    assert only.total_cost == pytest.approx(10.0)
    assert [b.model_name for b in only.model_breakdowns] == ["claude-opus-4-7"]
    # Top-level totals also re-summed.
    assert filtered.totals.total_cost == pytest.approx(10.0)


def test_filter_daily_by_models_drops_entries_with_no_match() -> None:
    breakdowns = [_breakdown("claude-opus-4-7", cost=10.0)]
    report = _report(
        [
            _entry(
                "2026-05-16",
                total_cost=10.0,
                models=["claude-opus-4-7"],
                model_breakdowns=breakdowns,
            )
        ]
    )
    filtered = filter_daily_by_models(report, ["claude-haiku-4-5-20251001"])
    assert filtered.daily == []


def test_filter_daily_by_models_empty_selection_is_passthrough() -> None:
    report = _report([_entry("2026-05-16", total_cost=5.0)])
    assert filter_daily_by_models(report, []) is report
    assert filter_daily_by_models(report, None) is report  # type: ignore[arg-type]


# ---------- available_models ----------


def test_available_models_unique_sorted() -> None:
    report = _report(
        [
            _entry("2026-05-15", models=["claude-opus-4-7", "claude-haiku-4-5"]),
            _entry("2026-05-16", models=["claude-opus-4-7"]),
        ]
    )
    assert available_models(report) == ["claude-haiku-4-5", "claude-opus-4-7"]


def test_available_models_empty_report() -> None:
    assert available_models(_report([])) == []


# ---------- daily_cache_hit_ratio ----------


def test_daily_cache_hit_ratio_per_day_in_order() -> None:
    report = _report(
        [
            _entry(
                "2026-05-15",
                input_tokens=10,
                cache_creation_tokens=10,
                cache_read_tokens=80,
            ),
            _entry(
                "2026-05-13",
                input_tokens=100,
                cache_creation_tokens=0,
                cache_read_tokens=0,
            ),
        ]
    )
    series = daily_cache_hit_ratio(report)
    # Sorted ascending by date.
    assert [d for d, _ in series] == ["2026-05-13", "2026-05-15"]
    assert series[0][1] == 0.0
    assert series[1][1] == pytest.approx(0.8)


def test_daily_cache_hit_ratio_empty_report() -> None:
    assert daily_cache_hit_ratio(_report([])) == []


# ---------- token_flow_sankey_data ----------


def test_token_flow_sankey_data_two_families() -> None:
    opus_b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        cost=5.0,
    )
    haiku_b = ModelBreakdown(
        modelName="claude-haiku-4-5",
        inputTokens=1,
        outputTokens=2,
        cacheCreationTokens=3,
        cacheReadTokens=4,
        cost=0.5,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                models=["claude-opus-4-7", "claude-haiku-4-5"],
                model_breakdowns=[opus_b, haiku_b],
                total_cost=5.5,
            )
        ]
    )
    data_ = token_flow_sankey_data(report)
    # 4 kinds + 2 families = 6 labels.
    assert len(data_["labels"]) == 6
    assert data_["labels"][:4] == ["input", "output", "cache_create", "cache_read"]
    # Family labels carry cost. Sorted alphabetically (haiku before opus).
    assert data_["labels"][4].startswith("haiku ($")
    assert "$0.50" in data_["labels"][4]
    assert data_["labels"][5].startswith("opus ($")
    assert "$5.00" in data_["labels"][5]
    # 4 kinds × 2 families = 8 nonzero links.
    assert len(data_["sources"]) == 8
    assert all(s in range(4) for s in data_["sources"])
    assert all(t in (4, 5) for t in data_["targets"])
    # Total link value equals total tokens across both models.
    expected_total_tokens = (
        opus_b.input_tokens
        + opus_b.output_tokens
        + opus_b.cache_creation_tokens
        + opus_b.cache_read_tokens
        + haiku_b.input_tokens
        + haiku_b.output_tokens
        + haiku_b.cache_creation_tokens
        + haiku_b.cache_read_tokens
    )
    assert sum(data_["values"]) == expected_total_tokens


def test_token_flow_sankey_data_skips_zero_links() -> None:
    """Kind→family links with zero tokens are omitted to keep the diagram clean."""
    only_input = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=100,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=0,
        cost=1.0,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=[only_input],
                total_cost=1.0,
                input_tokens=100,
                output_tokens=0,
                cache_creation_tokens=0,
                cache_read_tokens=0,
            )
        ]
    )
    data_ = token_flow_sankey_data(report)
    # Only the input→opus link survives.
    assert data_["values"] == [100]
    assert data_["sources"] == [0]  # "input" kind index
    assert data_["targets"] == [4]  # first family node


def test_token_flow_sankey_data_empty_report() -> None:
    data_ = token_flow_sankey_data(_report([]))
    assert data_["labels"] == []
    assert data_["values"] == []
    assert data_["customdata"] == []
    assert data_["value_mode"] == "tokens"


def test_token_flow_sankey_data_customdata_carries_tokens_and_cost() -> None:
    """Every link carries (absolute_tokens, family_total_cost) for the hover."""
    b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        cost=5.0,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=[b],
                total_cost=5.0,
            )
        ]
    )
    data_ = token_flow_sankey_data(report)
    # 4 links (one per kind), each carrying (tokens, family_cost).
    assert len(data_["customdata"]) == 4
    for tokens, cost in data_["customdata"]:
        assert tokens > 0
        assert cost == pytest.approx(5.0)


def test_token_flow_sankey_data_cost_mode_widths_sum_to_window_cost() -> None:
    """In cost mode, total link width equals total window cost (within rounding).
    Proportional attribution conserves cost at family level."""
    opus = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        cost=10.0,
    )
    haiku = ModelBreakdown(
        modelName="claude-haiku-4-5",
        inputTokens=1,
        outputTokens=2,
        cacheCreationTokens=3,
        cacheReadTokens=4,
        cost=1.0,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=[opus, haiku],
                models=["claude-opus-4-7", "claude-haiku-4-5"],
                total_cost=11.0,
            )
        ]
    )
    data_ = token_flow_sankey_data(report, value_mode="cost")
    assert sum(data_["values"]) == pytest.approx(11.0, rel=1e-6)
    assert data_["value_mode"] == "cost"


def test_token_flow_sankey_data_invalid_mode_raises() -> None:
    with pytest.raises(ValueError):
        token_flow_sankey_data(_report([]), value_mode="dollars")


def test_token_flow_sankey_data_top_n_collapses_into_others() -> None:
    """top_n=2 keeps the 2 most-expensive families and folds the rest into Others."""
    families = [
        ("claude-opus-4-7", 100.0),
        ("claude-haiku-4-5", 50.0),
        ("claude-sonnet-4-6", 10.0),
        ("claude-3-5-sonnet", 5.0),
    ]
    breakdowns = [
        ModelBreakdown(
            modelName=name,
            inputTokens=10,
            outputTokens=10,
            cacheCreationTokens=10,
            cacheReadTokens=10,
            cost=cost,
        )
        for name, cost in families
    ]
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=breakdowns,
                models=[n for n, _ in families],
                total_cost=sum(c for _, c in families),
            )
        ]
    )
    data_ = token_flow_sankey_data(report, top_n=2)
    # 4 kinds + (top 2 families + "Others") = 7 labels.
    assert len(data_["labels"]) == 7
    family_labels = data_["labels"][4:]
    # Top 2 by cost: opus (100) and haiku (50). Then Others = sonnet+legacy = 15.
    assert any(lbl.startswith("Others ($") for lbl in family_labels)
    assert any(lbl.startswith("opus ($100.00)") for lbl in family_labels)
    assert any(lbl.startswith("haiku ($50.00)") for lbl in family_labels)
    others_label = next(lbl for lbl in family_labels if lbl.startswith("Others"))
    assert "$15.00" in others_label


def test_token_flow_sankey_data_top_n_larger_than_families_is_noop() -> None:
    """top_n bigger than the actual count → no Others node, no collapse."""
    b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=10,
        outputTokens=10,
        cacheCreationTokens=10,
        cacheReadTokens=10,
        cost=1.0,
    )
    report = _report([_entry("2026-05-16", model_breakdowns=[b], total_cost=1.0)])
    data_ = token_flow_sankey_data(report, top_n=10)
    assert not any("Others" in lbl for lbl in data_["labels"])


# ---------- model_breakdown ----------


def test_model_breakdown_sorted_desc_with_share() -> None:
    opus = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=100,
        outputTokens=200,
        cacheCreationTokens=300,
        cacheReadTokens=400,
        cost=10.0,
    )
    haiku = ModelBreakdown(
        modelName="claude-haiku-4-5-20251001",
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        cost=1.0,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=[opus, haiku],
                models=["claude-opus-4-7", "claude-haiku-4-5-20251001"],
                total_cost=11.0,
            )
        ]
    )
    rows = model_breakdown(report)
    # Sorted by cost desc.
    assert [r["model"] for r in rows] == [
        "claude-opus-4-7",
        "claude-haiku-4-5-20251001",
    ]
    # Full model names preserved (no family collapse).
    assert rows[0]["family"] == "opus"
    assert rows[1]["family"] == "haiku"
    # Share sums to 1.
    assert rows[0]["share"] + rows[1]["share"] == pytest.approx(1.0)
    # $/MTok blended is cost / tokens × 1M.
    assert rows[0]["per_mtok"] == pytest.approx(10.0 / 1000 * 1_000_000)


def test_model_breakdown_keeps_versions_separate() -> None:
    """Regression: collapsing opus-4-6 and opus-4-7 into "opus" would hide
    version-comparison work. The breakdown keeps the full model name."""
    b46 = ModelBreakdown(
        modelName="claude-opus-4-6",
        inputTokens=1,
        outputTokens=1,
        cacheCreationTokens=1,
        cacheReadTokens=1,
        cost=2.0,
    )
    b47 = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=1,
        outputTokens=1,
        cacheCreationTokens=1,
        cacheReadTokens=1,
        cost=3.0,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=[b46, b47],
                models=["claude-opus-4-6", "claude-opus-4-7"],
                total_cost=5.0,
            )
        ]
    )
    rows = model_breakdown(report)
    names = [r["model"] for r in rows]
    assert "claude-opus-4-6" in names
    assert "claude-opus-4-7" in names


def test_model_breakdown_empty_report() -> None:
    assert model_breakdown(_report([])) == []


HOME_SLUG = "-Users-quintin-johnsmith"


def test_friendly_project_label_home_dir_itself() -> None:
    """Regression for the johnsmith bug: the home directory slug must NOT
    be rendered as 'johnsmith — Users/quintin'."""
    assert friendly_project_label(HOME_SLUG, home_slug=HOME_SLUG) == "~"


def test_friendly_project_label_under_home() -> None:
    assert (
        friendly_project_label(
            "-Users-quintin-johnsmith-Documents-RiderProjects-WorldForge",
            home_slug=HOME_SLUG,
        )
        == "~/Documents-RiderProjects-WorldForge"
    )


def test_friendly_project_label_under_home_hyphenated_dir() -> None:
    """Hyphenated directory name (mini-ollama-ui) survives verbatim — we
    can't recover its structure from the slug but we shouldn't mangle it."""
    assert (
        friendly_project_label(
            "-Users-quintin-johnsmith-Downloads-mini-ollama-ui",
            home_slug=HOME_SLUG,
        )
        == "~/Downloads-mini-ollama-ui"
    )


def test_friendly_project_label_outside_home() -> None:
    """Path not under home → strip leading dash, leave the rest verbatim."""
    assert (
        friendly_project_label(
            "-Volumes-SSK-Drive--ManageLiterature", home_slug=HOME_SLUG
        )
        == "Volumes-SSK-Drive--ManageLiterature"
    )


def test_friendly_project_label_no_home_slug() -> None:
    """Without home info, we just strip the leading dash."""
    assert (
        friendly_project_label("-Users-anyone-Documents-Foo")
        == "Users-anyone-Documents-Foo"
    )


def test_friendly_project_label_passthrough() -> None:
    assert friendly_project_label("Unknown Project") == "Unknown Project"
    assert friendly_project_label("") == ""


def test_friendly_project_label_home_lookalike() -> None:
    """A different user's home should not match — we require exact prefix."""
    result = friendly_project_label(
        "-Users-jane-Documents-Hack", home_slug=HOME_SLUG
    )
    # No home match → fall back to leading-dash strip.
    assert result == "Users-jane-Documents-Hack"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-opus-4-7", "claude-opus-4-7"),  # no date suffix → unchanged
        ("claude-opus-4-6", "claude-opus-4-6"),
        ("claude-3-5-sonnet-20240620", "claude-3-5-sonnet"),
        ("gpt-4o", "gpt-4o"),  # not claude-prefixed → passthrough
        ("", ""),
    ],
)
def test_short_model_label(name: str, expected: str) -> None:
    assert short_model_label(name) == expected


def test_model_breakdown_zero_tokens_safe() -> None:
    """Defensive: a model with zero recorded tokens shouldn't divide by zero."""
    weird = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=0,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=0,
        cost=0.0,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=[weird],
                models=["claude-opus-4-7"],
                total_cost=0.0,
                input_tokens=0,
                output_tokens=0,
                cache_creation_tokens=0,
                cache_read_tokens=0,
            )
        ]
    )
    rows = model_breakdown(report)
    assert rows[0]["per_mtok"] == 0.0
    assert rows[0]["share"] == 0.0


# ---------- prior_window_query ----------


def test_prior_window_query_shifts_back_by_window_length() -> None:
    q = Query(since="20260417", until="20260516")
    prior = prior_window_query(q)
    # 30-day window → prior is 20260318 → 20260416 (30 days ending day before).
    assert prior is not None
    assert prior.since == "20260318"
    assert prior.until == "20260416"


def test_prior_window_query_carries_project_and_offline() -> None:
    q = Query(since="20260501", until="20260516", project="x", offline=True)
    prior = prior_window_query(q)
    assert prior is not None
    assert prior.project == "x"
    assert prior.offline is True


def test_prior_window_query_returns_none_without_bounds() -> None:
    assert prior_window_query(Query()) is None
    assert prior_window_query(Query(since="20260501")) is None
    assert prior_window_query(Query(until="20260516")) is None


def test_prior_window_query_handles_malformed_dates() -> None:
    assert prior_window_query(Query(since="bad", until="20260516")) is None


def test_prior_window_query_single_day() -> None:
    q = Query(since="20260516", until="20260516")
    prior = prior_window_query(q)
    assert prior is not None
    assert prior.since == "20260515"
    assert prior.until == "20260515"


def test_prior_window_query_rejects_inverted_range() -> None:
    """Until earlier than since → invalid range, no prior to compute."""
    assert prior_window_query(Query(since="20260601", until="20260501")) is None


# ---------- window_effective_per_mtok ----------


def test_window_effective_per_mtok_blended_rate() -> None:
    """Effective rate = total cost / total tokens × 1M. Caches pull this
    down — a window with 99% cache reads but full input pricing should
    end up well below the per-token input rate."""
    report = _report(
        [
            _entry(
                "2026-05-16",
                input_tokens=1_000,
                output_tokens=1_000,
                cache_creation_tokens=1_000,
                cache_read_tokens=997_000,
                total_cost=1.0,
            )
        ]
    )
    # 1M total tokens, $1 cost → $1/MTok effective.
    assert window_effective_per_mtok(report) == pytest.approx(1.0)


def test_window_effective_per_mtok_empty_report() -> None:
    assert window_effective_per_mtok(_report([])) is None


def test_window_effective_per_mtok_zero_tokens() -> None:
    """A report with entries but no tokens → no division possible → None."""
    report = _report(
        [
            _entry(
                "2026-05-16",
                input_tokens=0,
                output_tokens=0,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                total_cost=0.0,
            )
        ]
    )
    assert window_effective_per_mtok(report) is None


# ---------- typical_burn_rate ----------


def test_typical_burn_rate_median_of_completed_blocks() -> None:
    rep = BlocksReport(
        blocks=[
            _block(block_id="b1", is_active=False, cost_per_hour=4.0),
            _block(block_id="b2", is_active=False, cost_per_hour=8.0),
            _block(block_id="b3", is_active=False, cost_per_hour=12.0),
        ]
    )
    assert typical_burn_rate(rep) == pytest.approx(8.0)


def test_typical_burn_rate_excludes_active_block() -> None:
    rep = BlocksReport(
        blocks=[
            _block(block_id="b1", is_active=False, cost_per_hour=4.0),
            _block(block_id="b2", is_active=False, cost_per_hour=8.0),
            _block(block_id="b3", is_active=False, cost_per_hour=12.0),
            # Active block sets a wildly high rate; must be ignored.
            _block(block_id="b-active", is_active=True, cost_per_hour=100.0),
        ]
    )
    assert typical_burn_rate(rep) == pytest.approx(8.0)


def test_typical_burn_rate_excludes_gap_blocks() -> None:
    rep = BlocksReport(
        blocks=[
            _block(block_id="b1", is_active=False, cost_per_hour=4.0),
            _block(block_id="b2", is_active=False, cost_per_hour=8.0),
            _block(block_id="b3", is_active=False, cost_per_hour=12.0),
            _block(block_id="gap", is_active=False, is_gap=True, cost_per_hour=999.0),
        ]
    )
    assert typical_burn_rate(rep) == pytest.approx(8.0)


def test_typical_burn_rate_too_few_samples() -> None:
    """Fewer than 3 completed blocks → no baseline (median is misleading)."""
    rep = BlocksReport(
        blocks=[
            _block(block_id="b1", is_active=False, cost_per_hour=4.0),
            _block(block_id="b2", is_active=False, cost_per_hour=8.0),
        ]
    )
    assert typical_burn_rate(rep) is None


def test_typical_burn_rate_no_burn_rate_field() -> None:
    """A completed block with burn_rate=None doesn't count."""
    rep = BlocksReport(
        blocks=[
            _block(block_id="b1", is_active=False, cost_per_hour=4.0),
            _block(block_id="b2", is_active=False, cost_per_hour=8.0),
            _block(block_id="b3", is_active=False, cost_per_hour=None),
        ]
    )
    # Only 2 valid samples → too few.
    assert typical_burn_rate(rep) is None


# ---------- cost_by_kind ----------


@pytest.fixture
def _stub_rates(monkeypatch):
    """Stub LiteLLM with a fixed rate table so test assertions don't drift
    if Anthropic updates rates upstream."""
    fake = {
        "claude-opus-4-7": {
            "input": 5.0, "output": 25.0,
            "cache_create": 6.25, "cache_read": 0.50,
        },
    }
    monkeypatch.setattr(
        "tokenscope.pricing.rates_for_model",
        lambda name: fake.get(name),
    )


def test_cost_by_kind_returns_four_kinds_with_share_summing_to_one(_stub_rates) -> None:
    b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=1_000_000,
        outputTokens=1_000_000,
        cacheCreationTokens=1_000_000,
        cacheReadTokens=1_000_000,
        cost=36.75,
    )
    report = _report(
        [
            _entry(
                "2026-05-16",
                model_breakdowns=[b],
                cache_read_tokens=1_000_000,
                cache_creation_tokens=1_000_000,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                total_cost=36.75,
            )
        ]
    )
    rows = cost_by_kind(report)
    assert rows is not None
    assert [r["kind"] for r in rows] == [
        "input",
        "output",
        "cache_create",
        "cache_read",
    ]
    assert rows[0]["est_cost"] == pytest.approx(5.0)
    assert rows[1]["est_cost"] == pytest.approx(25.0)
    assert rows[2]["est_cost"] == pytest.approx(6.25)
    assert rows[3]["est_cost"] == pytest.approx(0.50)
    assert sum(r["share"] for r in rows) == pytest.approx(1.0)


def test_cost_by_kind_empty_report(_stub_rates) -> None:
    """Empty report → 0 in every cell, *not* None (None signals
    'rates unavailable', not 'no data')."""
    rows = cost_by_kind(_report([]))
    assert rows is not None
    assert [r["tokens"] for r in rows] == [0, 0, 0, 0]
    assert [r["est_cost"] for r in rows] == [0.0, 0.0, 0.0, 0.0]


def test_cost_by_kind_returns_none_when_rates_unavailable(monkeypatch) -> None:
    """Offline + no cache → rates_for_model returns None for every model.
    cost_by_kind should signal 'hide the panel' by returning None rather
    than rendering zeros that read like 'no usage'."""
    monkeypatch.setattr("tokenscope.pricing.rates_for_model", lambda _name: None)
    b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=1_000_000, outputTokens=0,
        cacheCreationTokens=0, cacheReadTokens=0,
        cost=5.0,
    )
    report = _report(
        [_entry("2026-05-16", model_breakdowns=[b], input_tokens=1_000_000, total_cost=5.0)]
    )
    assert cost_by_kind(report) is None


# ---------- blocks_for_session (Slice 17) ----------


def test_blocks_for_session_matches_last_activity_day() -> None:
    """Slice 17: same-day proximity heuristic — blocks that started on
    the session's last-activity date show up; others don't."""
    sess = _session("sess-a", "2026-05-16")
    rep = BlocksReport(
        blocks=[
            _block(block_id="b-on-day", start_time="2026-05-16T13:00:00.000Z"),
            _block(block_id="b-prev-day", start_time="2026-05-15T13:00:00.000Z"),
            _block(block_id="b-next-day", start_time="2026-05-17T13:00:00.000Z"),
        ]
    )
    matched = blocks_for_session(rep, sess)
    assert [b.id for b in matched] == ["b-on-day"]


def test_blocks_for_session_respects_tz() -> None:
    """When the user is in a westerly tz, a UTC-late-night block flips
    to the previous local day."""
    sess = _session("sess-a", "2026-05-16")
    rep = BlocksReport(
        blocks=[
            # 02:00 UTC on May 17 is May 16 19:00 in Los Angeles.
            _block(block_id="b-late-utc", start_time="2026-05-17T02:00:00.000Z"),
        ]
    )
    matched = blocks_for_session(rep, sess, tz="America/Los_Angeles")
    assert [b.id for b in matched] == ["b-late-utc"]


def test_blocks_for_session_empty_when_no_match() -> None:
    sess = _session("sess-a", "2026-05-16")
    rep = BlocksReport(
        blocks=[_block(block_id="b1", start_time="2026-05-15T13:00:00.000Z")]
    )
    assert blocks_for_session(rep, sess) == []


def test_blocks_for_session_excludes_gap_blocks() -> None:
    sess = _session("sess-a", "2026-05-16")
    rep = BlocksReport(
        blocks=[
            _block(block_id="b-real", start_time="2026-05-16T13:00:00.000Z"),
            _block(
                block_id="b-gap",
                start_time="2026-05-16T18:00:00.000Z",
                is_gap=True,
            ),
        ]
    )
    matched = blocks_for_session(rep, sess)
    assert [b.id for b in matched] == ["b-real"]


# ---------- format_compact_int ----------


def test_format_compact_int_thousand_separators_under_1m() -> None:
    """Sub-1M stays comma-grouped — `7,358` reads cleaner than `7.4K`
    at dashboard magnitudes."""
    assert format_compact_int(0) == "0"
    assert format_compact_int(7) == "7"
    assert format_compact_int(7_358) == "7,358"
    assert format_compact_int(999_999) == "999,999"


def test_format_compact_int_million_and_billion() -> None:
    assert format_compact_int(1_000_000) == "1.0M"
    assert format_compact_int(4_911_389) == "4.9M"
    assert format_compact_int(15_697_744) == "15.7M"
    assert format_compact_int(1_000_000_000) == "1.00B"
    assert format_compact_int(1_602_177_029) == "1.60B"
    assert format_compact_int(2_321_335_452) == "2.32B"


def test_format_compact_int_negative_mirror() -> None:
    """Defensive: negative input keeps the sign, formats the absolute
    value through the same scale."""
    assert format_compact_int(-7_358) == "-7,358"
    assert format_compact_int(-4_911_389) == "-4.9M"


# ---------- spike_day ----------


def test_spike_day_returns_none_for_short_windows() -> None:
    """A 2-day window has too few points to compute a meaningful
    median + threshold. Bail out rather than annotate noise."""
    rep = _report(
        [
            _entry("2026-05-15", total_cost=10.0),
            _entry("2026-05-16", total_cost=50.0),
        ]
    )
    assert spike_day(rep, threshold_multiplier=3.0) is None


def test_spike_day_returns_none_when_no_outlier() -> None:
    """All days within `3 × median` → no spike to annotate."""
    rep = _report(
        [_entry(f"2026-05-{d:02d}", total_cost=10.0) for d in range(10, 20)]
    )
    assert spike_day(rep, threshold_multiplier=3.0) is None


def test_spike_day_identifies_outlier_above_threshold() -> None:
    """One day at ~10× median = clear spike. Returns (date, cost)."""
    rep = _report(
        [_entry(f"2026-05-{d:02d}", total_cost=10.0) for d in range(10, 18)]
        + [_entry("2026-04-18", total_cost=400.0)]
    )
    spike = spike_day(rep, threshold_multiplier=3.0)
    assert spike == ("2026-04-18", 400.0)


def test_spike_day_picks_highest_when_multiple_spikes_qualify() -> None:
    """Multiple days exceed the threshold — return the most extreme
    so the chart annotation calls out the loudest signal."""
    rep = _report(
        [_entry(f"2026-05-{d:02d}", total_cost=10.0) for d in range(10, 18)]
        + [
            _entry("2026-04-18", total_cost=200.0),
            _entry("2026-04-19", total_cost=400.0),
        ]
    )
    spike = spike_day(rep, threshold_multiplier=3.0)
    assert spike == ("2026-04-19", 400.0)


def test_spike_day_threshold_multiplier_drives_sensitivity() -> None:
    """Same data, two thresholds — looser threshold flags the day,
    stricter one does not."""
    rep = _report(
        [_entry(f"2026-05-{d:02d}", total_cost=10.0) for d in range(10, 18)]
        + [_entry("2026-04-18", total_cost=25.0)]
    )
    # 25 vs median ~10 → 2.5×.  3× threshold leaves it alone.
    assert spike_day(rep, threshold_multiplier=3.0) is None
    # 2× threshold catches it.
    assert spike_day(rep, threshold_multiplier=2.0) == ("2026-04-18", 25.0)


def test_spike_day_zero_median_returns_none() -> None:
    """If every day is zero, there's no meaningful threshold; bail."""
    rep = _report(
        [_entry(f"2026-05-{d:02d}", total_cost=0.0) for d in range(10, 18)]
    )
    assert spike_day(rep, threshold_multiplier=3.0) is None


# ---------- overview_insight ----------


def test_overview_insight_headline_includes_total_and_window() -> None:
    text = overview_insight(
        window_total_cost=1_020.73,
        window_days=30,
        prior_total=None,
        spike=None,
        cache_hit_ratio=0.0,
    )
    assert "1,020.73" in text
    assert "30 days" in text


def test_overview_insight_appends_prior_period_comparison() -> None:
    """When prior_total is provided, the headline gains a `, up X% vs
    prior N days` continuation."""
    text = overview_insight(
        window_total_cost=2000.0,
        window_days=30,
        prior_total=1000.0,
        spike=None,
        cache_hit_ratio=0.0,
    )
    assert "up 100% vs the prior 30 days" in text


def test_overview_insight_uses_down_for_decreased_cost() -> None:
    text = overview_insight(
        window_total_cost=500.0,
        window_days=30,
        prior_total=1000.0,
        spike=None,
        cache_hit_ratio=0.0,
    )
    assert "down 50%" in text


def test_overview_insight_omits_prior_when_unknown() -> None:
    """No prior data → drop the comparison sentence rather than
    surface a null. Headline stays single-sentence."""
    text = overview_insight(
        window_total_cost=500.0,
        window_days=30,
        prior_total=None,
        spike=None,
        cache_hit_ratio=0.0,
    )
    assert "vs the prior" not in text


def test_overview_insight_includes_spike_sentence_when_provided() -> None:
    text = overview_insight(
        window_total_cost=1_000.0,
        window_days=30,
        prior_total=None,
        spike=("2026-04-18", 400.0),
        cache_hit_ratio=0.0,
    )
    assert "2026-04-18" in text
    assert "400.00" in text
    # 400/1000 = 40% of the window.
    assert "40%" in text


def test_overview_insight_includes_cache_sentence_when_ratio_nonzero() -> None:
    text = overview_insight(
        window_total_cost=1_000.0,
        window_days=30,
        prior_total=None,
        spike=None,
        cache_hit_ratio=0.992,
    )
    assert "99.2%" in text
    assert "Cache reads" in text


def test_overview_insight_omits_cache_sentence_at_zero() -> None:
    """Zero cache-hit ratio = nothing to say about caching. Drop
    the sentence rather than surface "0.0%"."""
    text = overview_insight(
        window_total_cost=1_000.0,
        window_days=30,
        prior_total=None,
        spike=None,
        cache_hit_ratio=0.0,
    )
    assert "Cache reads" not in text


def test_overview_insight_spike_skipped_on_zero_window_cost() -> None:
    """Defensive: window_total_cost=0 → spike share is undefined.
    Drop the spike sentence rather than surface a nonsense ratio."""
    text = overview_insight(
        window_total_cost=0.0,
        window_days=30,
        prior_total=None,
        spike=("2026-04-18", 0.0),
        cache_hit_ratio=0.0,
    )
    assert "2026-04-18" not in text


# ---------- bold_numbers_in_insight ----------


def test_bold_numbers_in_insight_wraps_dollar_amounts() -> None:
    """Dollar amounts (`$1,020.73`, `$303.06`) get wrapped in
    `<strong>` so the eye lands on the figures."""
    out = bold_numbers_in_insight("You spent $1,020.73 over 30 days.")
    assert "<strong>$1,020.73</strong>" in out


def test_bold_numbers_in_insight_wraps_unsigned_percentages() -> None:
    out = bold_numbers_in_insight("Cache reads served 99.0% of input-side tokens.")
    assert "<strong>99.0%</strong>" in out


def test_bold_numbers_in_insight_wraps_signed_percentages() -> None:
    """Signed percentages like `+91%` or `-15%` keep the sign inside
    the `<strong>` wrapping."""
    out = bold_numbers_in_insight("up 91% vs the prior 30 days")
    assert "<strong>91%</strong>" in out
    out_signed = bold_numbers_in_insight("+91% increase")
    assert "<strong>+91%</strong>" in out_signed
    out_neg = bold_numbers_in_insight("a -15% change")
    assert "<strong>-15%</strong>" in out_neg


def test_bold_numbers_in_insight_pass_through_when_no_match() -> None:
    """No numbers → no transformation. Don't introduce stray tags."""
    out = bold_numbers_in_insight("Cache reads dominate the window.")
    assert out == "Cache reads dominate the window."
    assert "<strong>" not in out


def test_bold_numbers_in_insight_handles_full_paragraph() -> None:
    """End-to-end: the actual insight paragraph from `overview_insight`
    gets every figure bolded without breaking the prose."""
    paragraph = overview_insight(
        window_total_cost=1_020.73,
        window_days=30,
        prior_total=535.0,
        spike=("2026-04-18", 303.06),
        cache_hit_ratio=0.990,
    )
    out = bold_numbers_in_insight(paragraph)
    for figure in ("$1,020.73", "$303.06", "99.0%"):
        assert f"<strong>{figure}</strong>" in out, (
            f"figure {figure!r} not bolded; output: {out!r}"
        )


# ---------- collapse_composition_rows ----------


def _comp_row(kind: str, share: float, *, tokens: int = 1000, est_cost: float = 1.0) -> dict:
    return {"kind": kind, "share": share, "tokens": tokens, "est_cost": est_cost}


def test_collapse_composition_rows_groups_below_threshold() -> None:
    """Rows with share below threshold group into a single 'other' row
    whose tokens / cost / share are the sum of the collapsed rows."""
    rows = [
        _comp_row("cache_read", 0.99, tokens=1_000_000, est_cost=900.0),
        _comp_row("input", 0.005, tokens=5_000, est_cost=4.0),
        _comp_row("cache_create", 0.004, tokens=4_000, est_cost=3.0),
        _comp_row("output", 0.001, tokens=1_000, est_cost=1.0),
    ]
    out = collapse_composition_rows(rows, hide_threshold=0.01)
    assert {r["kind"] for r in out} == {"cache_read", "other"}
    other = next(r for r in out if r["kind"] == "other")
    assert other["tokens"] == 5_000 + 4_000 + 1_000
    assert other["est_cost"] == pytest.approx(4.0 + 3.0 + 1.0)
    assert other["share"] == pytest.approx(0.005 + 0.004 + 0.001)


def test_collapse_composition_rows_no_op_when_nothing_below_threshold() -> None:
    """All rows ≥ threshold → input passes through unchanged
    (different list object, same content)."""
    rows = [
        _comp_row("a", 0.40),
        _comp_row("b", 0.30),
        _comp_row("c", 0.20),
        _comp_row("d", 0.10),
    ]
    out = collapse_composition_rows(rows, hide_threshold=0.01)
    assert out == rows
    assert out is not rows  # defensive copy


def test_collapse_composition_rows_keeps_single_small_row() -> None:
    """If only ONE row is below threshold, collapsing it into 'other'
    would be a relabel, not a simplification. Leave it alone."""
    rows = [
        _comp_row("big", 0.99),
        _comp_row("small", 0.01),
    ]
    out = collapse_composition_rows(rows, hide_threshold=0.05)
    assert out == rows


def test_collapse_composition_rows_zero_threshold_passes_through() -> None:
    rows = [_comp_row("a", 0.5), _comp_row("b", 0.5)]
    assert collapse_composition_rows(rows, hide_threshold=0.0) == rows


# ---------- format_timezone_for_display ----------


def test_format_timezone_for_display_replaces_underscores() -> None:
    """IANA identifiers use underscores; UI copy should show spaces."""
    assert format_timezone_for_display("America/New_York") == "America/New York"
    assert format_timezone_for_display("Pacific/Pago_Pago") == "Pacific/Pago Pago"


def test_format_timezone_for_display_passes_through_when_already_spaced() -> None:
    assert format_timezone_for_display("UTC") == "UTC"
    assert format_timezone_for_display("America/Chicago") == "America/Chicago"


def test_format_timezone_for_display_empty_returns_empty() -> None:
    """Defensive: missing tz string doesn't crash; returns empty."""
    assert format_timezone_for_display("") == ""


# ---------- KNOWN_MODEL_FAMILIES ----------


def test_known_model_families_lists_current_anthropic_families() -> None:
    """The dashboard reasons about families, not versions. The
    registry of currently-known Anthropic families is the source the
    chart layer consults for canonical brand colors — opus always
    indigo, sonnet always cyan, haiku always emerald."""
    from tokenscope.analytics import KNOWN_MODEL_FAMILIES

    assert KNOWN_MODEL_FAMILIES == ("opus", "sonnet", "haiku")
