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
    block_cache_hit_ratio,
    block_cost_by_kind,
    blocks_for_session,
    blocks_on_day,
    cache_data_range,
    cache_hit_ratio,
    cache_savings,
    cost_by_kind,
    cost_concentration_summary,
    daily_cache_savings,
    per_model_cache_performance,
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


# ---------- find_session disambiguation by (session_id, project_path) ----------
#
# `session_id` is not unique across projects (ccusage slugs each
# Claude Code project's `subagents/` directory as the same id).
# `find_session` resolves by the `(session_id, project_path)` tuple.
# These tests pin the four resolution branches end-to-end.


def _session_in_project(
    session_id: str, project_path: str, *, cost: float = 1.0
) -> SessionEntry:
    """SessionEntry with `project_path` explicitly set — the helper
    above (`_session`) hardcodes a single project so it can't
    construct the duplicate-id-different-project case the fix
    targets."""
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
        lastActivity="2026-05-16",
        projectPath=project_path,
    )


def test_find_session_returns_unambiguous_match_when_project_path_omitted() -> None:
    """Branch 3: `project_path` is None AND exactly one row matches
    the `session_id`. Returns that row — the lookup is unambiguous,
    so legacy shareable URLs without the disambiguator still
    resolve correctly."""
    rep = _session_report([
        _session_in_project("a", "/Users/q/projA", cost=4.0),
    ])
    assert find_session(rep, "a").total_cost == 4.0
    assert find_session(rep, "a", project_path=None).total_cost == 4.0


def test_find_session_disambiguates_by_project_when_session_ids_collide() -> None:
    """Branch 1: `project_path` is given AND a row matches both
    fields. Returns that exact row — never the first-by-id match
    that produced the routing bug."""
    rep = _session_report([
        _session_in_project("subagents", "/Users/q/projA", cost=5.0),
        _session_in_project("subagents", "/Users/q/projB", cost=3.0),
    ])
    a = find_session(rep, "subagents", project_path="/Users/q/projA")
    b = find_session(rep, "subagents", project_path="/Users/q/projB")
    assert a is not None and a.total_cost == 5.0
    assert b is not None and b.total_cost == 3.0
    # Sanity: the two are distinct objects (not the same row
    # returned twice due to a bug that ignored project_path).
    assert a.project_path != b.project_path


def test_find_session_returns_none_when_project_path_doesnt_match() -> None:
    """Branch 2: `project_path` is given AND no row matches both
    fields (session aged out or URL tampered). Returns None —
    failing closed instead of silently picking a wrong row by
    session_id alone."""
    rep = _session_report([
        _session_in_project("subagents", "/Users/q/projA"),
    ])
    assert find_session(rep, "subagents", project_path="/Users/q/projZ") is None


def test_find_session_returns_none_when_ambiguous_id_and_no_project() -> None:
    """Branch 4: `project_path` is None AND multiple rows share
    the `session_id`. Returns None — caller MUST disambiguate.

    This is the test that pins the fix against the original bug.
    Pre-fix `find_session(rep, "subagents")` returned the FIRST
    matching row (silently wrong). Post-fix it returns None so the
    session view shows the empty-state instead of misrouting the
    user to a different project's data."""
    rep = _session_report([
        _session_in_project("subagents", "/Users/q/projA"),
        _session_in_project("subagents", "/Users/q/projB"),
    ])
    assert find_session(rep, "subagents") is None
    assert find_session(rep, "subagents", project_path=None) is None


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


# ---------- block_cache_hit_ratio ----------


def _block_with_counts(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_create: int,
    cache_read: int,
    cost_usd: float = 1.0,
    models: list[str] | None = None,
) -> BlockEntry:
    """Active-block fixture with caller-controlled token counts.

    Distinct from the module-level `_block` helper (which hard-codes
    10/20/30/40 counts) — the cache_hit / cost_by_kind tests need to
    vary the counts to drive the formula across cases."""
    return BlockEntry(
        id="2026-05-16T13:00:00.000Z",
        startTime="2026-05-16T13:00:00.000Z",
        endTime="2026-05-16T18:00:00.000Z",
        actualEndTime=None,
        isActive=True,
        isGap=False,
        entries=1,
        tokenCounts=BlockTokenCounts(
            inputTokens=input_tokens,
            outputTokens=output_tokens,
            cacheCreationInputTokens=cache_create,
            cacheReadInputTokens=cache_read,
        ),
        totalTokens=input_tokens + output_tokens + cache_create + cache_read,
        costUSD=cost_usd,
        models=models or ["claude-opus-4-7"],
        burnRate=BurnRate(
            tokensPerMinute=1.0,
            tokensPerMinuteForIndicator=1.0,
            costPerHour=8.0,
        ),
        projection=None,
    )


def test_block_cache_hit_ratio_matches_formula() -> None:
    """Block cache hit ratio uses the SAME formula as the daily one
    (`cache_read / (input + cache_create + cache_read)`) but reads
    from `BlockTokenCounts`'s JSON-aliased field names. Output
    tokens are excluded from the denominator — they're produced by
    the model, not part of the cache decision."""
    block = _block_with_counts(
        input_tokens=100, output_tokens=999_999,
        cache_create=200, cache_read=700,
    )
    expected = 700 / (100 + 200 + 700)
    assert block_cache_hit_ratio(block) == pytest.approx(expected)


def test_block_cache_hit_ratio_zero_when_no_cache_eligible_tokens() -> None:
    """A block with only output tokens has no cache-eligible
    denominator — return 0.0, not a ZeroDivisionError."""
    block = _block_with_counts(
        input_tokens=0, output_tokens=500,
        cache_create=0, cache_read=0,
    )
    assert block_cache_hit_ratio(block) == 0.0


def test_block_cache_hit_ratio_one_when_all_reads_from_cache() -> None:
    """Pure cache_read case — the ratio is 1.0 (every cache-eligible
    token was served from cache, no fresh input or cache creation)."""
    block = _block_with_counts(
        input_tokens=0, output_tokens=0,
        cache_create=0, cache_read=5_000_000,
    )
    assert block_cache_hit_ratio(block) == pytest.approx(1.0)


# ---------- block_token_counts_by_kind (Slice B: promoted helper) -------


def test_block_token_counts_by_kind_returns_canonical_kinds_in_order() -> None:
    """Slice B public-API contract: the returned dict's keys equal
    `pricing.KINDS` EXACTLY, in the canonical order
    `input → output → cache_create → cache_read`.

    Insertion order is the contract three downstream consumers rely
    on: the Live KPI card order, the composition bar's segment
    order, and the mini-table's row order all come from iterating
    this dict. A regression that reordered the dict literal would
    flip the visual sequence on every Live-view surface."""
    from tokenscope.analytics import block_token_counts_by_kind
    from tokenscope.pricing import KINDS

    block = _block_with_counts(
        input_tokens=1, output_tokens=2,
        cache_create=3, cache_read=4,
    )
    result = block_token_counts_by_kind(block)
    assert list(result) == list(KINDS), (
        f"block_token_counts_by_kind keys must equal KINDS in canonical "
        f"order; got {list(result)!r} vs {list(KINDS)!r}"
    )


def test_block_token_counts_by_kind_maps_each_kind_to_correct_field() -> None:
    """Slice B field-mapping contract: each kind key reads from the
    correct `BlockTokenCounts` field — the swap-resistance the helper
    was promoted to enforce in one place rather than re-prove at
    every consumer.

    Distinct counts (11/22/33/44) ensure any field-name swap (e.g.
    `cache_create` accidentally reading `cache_read_input_tokens`)
    produces a wrong value at the helper boundary, not just at the
    rendered surface. The three pre-slice-B consumer regression
    tests in `test_live.py` (commit 61967ef) catch consumer-level
    regressions; this test catches helper-level regressions before
    they fan out."""
    from tokenscope.analytics import block_token_counts_by_kind

    block = _block_with_counts(
        input_tokens=11, output_tokens=22,
        cache_create=33, cache_read=44,
    )
    assert block_token_counts_by_kind(block) == {
        "input": 11,
        "output": 22,
        "cache_create": 33,
        "cache_read": 44,
    }


# ---------- block_cost_by_kind ----------


def test_block_cost_by_kind_rescales_to_actual_block_cost(_stub_rates) -> None:
    """The block reports an aggregate `cost_usd`; ccusage doesn't
    break out per-kind cost in block JSON. `block_cost_by_kind`
    derives the per-kind RATIO from LiteLLM rates, then rescales so
    the sum matches `block.cost_usd` exactly. The user can trust the
    totals to add up; only the kind-split is approximate."""
    block = _block_with_counts(
        input_tokens=1_000_000, output_tokens=1_000_000,
        cache_create=1_000_000, cache_read=1_000_000,
        cost_usd=10.0,
    )
    rows = block_cost_by_kind(block)
    assert rows is not None
    kinds = [r["kind"] for r in rows]
    assert kinds == ["input", "output", "cache_create", "cache_read"]
    total = sum(r["est_cost"] for r in rows)
    assert total == pytest.approx(block.cost_usd)
    # Rates: input=5, output=25, cache_create=6.25, cache_read=0.5.
    # Notional cost ratio reflects rate ratio when token counts are equal.
    notional = (5.0, 25.0, 6.25, 0.5)
    notional_total = sum(notional)
    for row, expected_share_notional in zip(rows, notional):
        assert row["share"] == pytest.approx(expected_share_notional / notional_total)


def test_block_cost_by_kind_returns_none_when_no_rates_resolve(monkeypatch) -> None:
    """Offline + no cache → no model in `block.models` has rates.
    The helper signals "hide the cost line" by returning None, NOT
    a row of zero est_costs that would look like 'this kind costs
    nothing' when in fact rates are unknown."""
    monkeypatch.setattr("tokenscope.pricing.rates_for_model", lambda _name: None)
    block = _block_with_counts(
        input_tokens=100, output_tokens=200,
        cache_create=300, cache_read=400,
    )
    assert block_cost_by_kind(block) is None


def test_block_cost_by_kind_falls_back_to_later_models_for_rates(_stub_rates) -> None:
    """When the first model in `block.models` has no rates, the
    helper walks the list to find one that does. Defensive — the
    block's first model might be an experimental id not yet in
    LiteLLM's table."""
    block = _block_with_counts(
        input_tokens=1_000_000, output_tokens=1_000_000,
        cache_create=1_000_000, cache_read=1_000_000,
        cost_usd=10.0,
        models=["unknown-experimental-id", "claude-opus-4-7"],
    )
    rows = block_cost_by_kind(block)
    assert rows is not None
    assert sum(r["est_cost"] for r in rows) == pytest.approx(block.cost_usd)


def test_block_cost_by_kind_returns_none_when_block_has_no_models(_stub_rates) -> None:
    """A block with `models=[]` (defensive — ccusage should always
    emit at least one) has no rate source. The helper returns None
    so the UI hides the cost line rather than pretending a value."""
    block = BlockEntry(
        id="2026-05-16T13:00:00.000Z",
        startTime="2026-05-16T13:00:00.000Z",
        endTime="2026-05-16T18:00:00.000Z",
        actualEndTime=None,
        isActive=True,
        isGap=False,
        entries=1,
        tokenCounts=BlockTokenCounts(
            inputTokens=100, outputTokens=200,
            cacheCreationInputTokens=300, cacheReadInputTokens=400,
        ),
        totalTokens=1000,
        costUSD=1.0,
        models=[],
        burnRate=None,
        projection=None,
    )
    assert block_cost_by_kind(block) is None


def test_block_cost_by_kind_no_tokens_returns_zero_shares(_stub_rates) -> None:
    """A brand-new block with zero tokens of every kind: shares =
    0.0 across the board, est_costs = 0.0 (the block has no cost
    yet either). NOT None — None is the "rates unavailable" signal."""
    block = _block_with_counts(
        input_tokens=0, output_tokens=0,
        cache_create=0, cache_read=0,
        cost_usd=0.0,
    )
    rows = block_cost_by_kind(block)
    assert rows is not None
    assert [r["share"] for r in rows] == [0.0, 0.0, 0.0, 0.0]
    assert [r["est_cost"] for r in rows] == [0.0, 0.0, 0.0, 0.0]


# ---------- cache_savings ----------


@pytest.fixture
def _stub_cache_rates(monkeypatch):
    """Stub LiteLLM with a fixed rate table so savings assertions
    are deterministic against the (input − cache_read) delta."""
    fake = {
        "claude-opus-4-7": {
            "input": 15.0, "output": 75.0,
            "cache_create": 18.75, "cache_read": 1.50,
        },
        "claude-haiku-4-5-20251001": {
            "input": 1.0, "output": 5.0,
            "cache_create": 1.25, "cache_read": 0.10,
        },
    }
    monkeypatch.setattr(
        "tokenscope.pricing.rates_for_model",
        lambda name: fake.get(name),
    )


def test_cache_savings_calculation_matches_expected(_stub_cache_rates) -> None:
    """Savings = (input_rate − cache_read_rate) × cache_read_tokens / 1M,
    summed across breakdowns. For opus with the stub rates (15.0 − 1.5
    = 13.5) and 1M cache_read tokens, that's $13.50. The headline
    figure must reflect that delta — NOT the full input rate, which
    was the framing problem the user pulled in slice 12."""
    b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=0, outputTokens=0,
        cacheCreationTokens=0, cacheReadTokens=1_000_000,
        cost=2.0,
    )
    report = _report([
        _entry(
            "2026-05-16",
            model_breakdowns=[b],
            cache_read_tokens=1_000_000,
            cache_creation_tokens=0,
            input_tokens=0,
            output_tokens=0,
            total_cost=2.0,
        )
    ])
    result = cache_savings(report)
    assert result is not None
    assert result["savings_usd"] == pytest.approx(13.5)
    assert result["actual_cost_usd"] == pytest.approx(2.0)
    assert result["uncached_cost_usd"] == pytest.approx(15.5)


def test_cache_savings_sums_across_models_and_days(_stub_cache_rates) -> None:
    """Multiple models on multiple days → savings sum across every
    breakdown."""
    b_opus = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=0, outputTokens=0,
        cacheCreationTokens=0, cacheReadTokens=1_000_000,
        cost=1.0,
    )
    b_haiku = ModelBreakdown(
        modelName="claude-haiku-4-5-20251001",
        inputTokens=0, outputTokens=0,
        cacheCreationTokens=0, cacheReadTokens=2_000_000,
        cost=0.5,
    )
    report = _report([
        _entry(
            "2026-05-15",
            model_breakdowns=[b_opus],
            cache_read_tokens=1_000_000, cache_creation_tokens=0,
            input_tokens=0, output_tokens=0, total_cost=1.0,
        ),
        _entry(
            "2026-05-16",
            model_breakdowns=[b_haiku],
            cache_read_tokens=2_000_000, cache_creation_tokens=0,
            input_tokens=0, output_tokens=0, total_cost=0.5,
        ),
    ])
    result = cache_savings(report)
    assert result is not None
    # Opus: (15 − 1.5) × 1M / 1M = 13.5
    # Haiku: (1 − 0.1) × 2M / 1M = 1.8
    assert result["savings_usd"] == pytest.approx(13.5 + 1.8)
    assert result["actual_cost_usd"] == pytest.approx(1.5)
    assert result["uncached_cost_usd"] == pytest.approx(1.5 + 13.5 + 1.8)


def test_cache_savings_returns_none_when_no_rates(monkeypatch) -> None:
    """Offline + no cache → no rates resolve → return None so the
    UI hides the hero rather than rendering made-up zeros."""
    monkeypatch.setattr("tokenscope.pricing.rates_for_model", lambda _name: None)
    report = _report([
        _entry("2026-05-16", cache_read_tokens=1_000_000)
    ])
    assert cache_savings(report) is None


def test_cache_savings_empty_report_returns_none(_stub_cache_rates) -> None:
    """An empty window has no breakdowns to walk — no rates
    resolve, return None. The empty-window banner on the Cache
    view is the user-facing signal, not zero savings."""
    assert cache_savings(_report([])) is None


# ---------- daily_cache_savings ----------


def test_daily_cache_savings_per_day_rows(_stub_cache_rates) -> None:
    """One row per date in ascending order; each row's `savings_usd`
    matches the delta-rate formula applied to THAT day only."""
    b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=0, outputTokens=0,
        cacheCreationTokens=0, cacheReadTokens=1_000_000,
        cost=1.0,
    )
    report = _report([
        _entry(
            "2026-05-15",
            model_breakdowns=[b],
            cache_read_tokens=1_000_000, cache_creation_tokens=0,
            input_tokens=0, output_tokens=0, total_cost=1.0,
        ),
        _entry(
            "2026-05-16",
            model_breakdowns=[b],
            cache_read_tokens=1_000_000, cache_creation_tokens=0,
            input_tokens=0, output_tokens=0, total_cost=1.0,
        ),
    ])
    rows = daily_cache_savings(report)
    assert rows is not None
    dates = [r["date"] for r in rows]
    assert dates == ["2026-05-15", "2026-05-16"]
    for row in rows:
        assert row["savings_usd"] == pytest.approx(13.5)


def test_daily_cache_savings_returns_none_when_no_rates(monkeypatch) -> None:
    monkeypatch.setattr("tokenscope.pricing.rates_for_model", lambda _name: None)
    report = _report([_entry("2026-05-16", cache_read_tokens=1_000_000)])
    assert daily_cache_savings(report) is None


# ---------- per_model_cache_performance ----------


def test_per_model_cache_performance_aggregates_per_model(_stub_cache_rates) -> None:
    """Tokens summed per `model_name` across days; cache_hit_ratio
    + savings computed against the aggregated totals.

    Locks the `test_cache_handles_single_model_gracefully` contract
    indirectly: the helper still returns the single-model row, but
    the UI layer suppresses the section based on `len(rows) < 2`."""
    b_a = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=100, outputTokens=0,
        cacheCreationTokens=200, cacheReadTokens=1_000_000,
        cost=1.0,
    )
    b_b = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=400, outputTokens=0,
        cacheCreationTokens=300, cacheReadTokens=0,
        cost=0.5,
    )
    report = _report([
        _entry(
            "2026-05-15",
            model_breakdowns=[b_a],
            cache_read_tokens=1_000_000, cache_creation_tokens=200,
            input_tokens=100, output_tokens=0, total_cost=1.0,
        ),
        _entry(
            "2026-05-16",
            model_breakdowns=[b_b],
            cache_read_tokens=0, cache_creation_tokens=300,
            input_tokens=400, output_tokens=0, total_cost=0.5,
        ),
    ])
    rows = per_model_cache_performance(report)
    assert rows is not None
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "claude-opus-4-7"
    assert row["cache_read_tokens"] == 1_000_000
    assert row["cache_create_tokens"] == 500
    cache_eligible = 100 + 400 + 500 + 1_000_000
    assert row["cache_hit_ratio"] == pytest.approx(1_000_000 / cache_eligible)
    assert row["savings_usd"] == pytest.approx(13.5)
    assert row["has_rates"] is True


def test_per_model_cache_performance_flags_missing_rates(_stub_cache_rates) -> None:
    """A model whose name doesn't resolve to a rate keeps the
    counts + ratio but reports `has_rates=False` and savings=0.0
    — the UI renders the savings cell as `—` for that row."""
    b = ModelBreakdown(
        modelName="unknown-experimental-model",
        inputTokens=100, outputTokens=0,
        cacheCreationTokens=0, cacheReadTokens=1_000_000,
        cost=1.0,
    )
    report = _report([
        _entry(
            "2026-05-15",
            model_breakdowns=[b],
            cache_read_tokens=1_000_000, cache_creation_tokens=0,
            input_tokens=100, output_tokens=0, total_cost=1.0,
        )
    ])
    rows = per_model_cache_performance(report)
    assert rows is not None
    assert rows[0]["has_rates"] is False
    assert rows[0]["savings_usd"] == 0.0
    # The ratio still computes regardless of pricing availability.
    assert rows[0]["cache_hit_ratio"] == pytest.approx(
        1_000_000 / (100 + 1_000_000)
    )


def test_per_model_cache_performance_returns_none_for_empty_report() -> None:
    """No model breakdowns at all → return None so the UI hides
    the per-model panel rather than rendering an empty table."""
    assert per_model_cache_performance(_report([])) is None


# ---------- cache_data_range ----------


def test_cache_data_range_returns_first_and_last_cache_date() -> None:
    """Days with zero cache_create AND zero cache_read are skipped;
    the first/last with any cache activity bound the range."""
    report = _report([
        _entry(
            "2026-05-15",
            cache_read_tokens=0, cache_creation_tokens=0,
            input_tokens=100, output_tokens=200,
        ),
        _entry(
            "2026-05-16",
            cache_read_tokens=1_000, cache_creation_tokens=500,
        ),
        _entry(
            "2026-05-17",
            cache_read_tokens=0, cache_creation_tokens=300,
        ),
    ])
    assert cache_data_range(report) == ("2026-05-16", "2026-05-17")


def test_cache_data_range_returns_none_when_no_cache_activity() -> None:
    """Every entry has zero cache_create AND zero cache_read →
    return None, the banner is suppressed."""
    report = _report([
        _entry(
            "2026-05-15",
            cache_read_tokens=0, cache_creation_tokens=0,
            input_tokens=100, output_tokens=200,
        )
    ])
    assert cache_data_range(report) is None


# ---------- model_breakdown — extra columns (cache_hit + last_used) ------


def test_model_breakdown_carries_cache_hit_ratio_per_model() -> None:
    """The breakdown row now carries `cache_hit_ratio` per model
    so the Models view's table can render the column without a
    second analytics pass. Same formula as `cache_hit_ratio`,
    applied to this model's aggregated counts."""
    bd = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=100, outputTokens=200,
        cacheCreationTokens=300, cacheReadTokens=700,
        cost=5.0,
    )
    report = _report([
        _entry(
            "2026-05-15",
            model_breakdowns=[bd],
            input_tokens=100, output_tokens=200,
            cache_creation_tokens=300, cache_read_tokens=700,
            total_cost=5.0,
        )
    ])
    rows = model_breakdown(report)
    assert rows[0]["cache_hit_ratio"] == pytest.approx(
        700 / (100 + 300 + 700)
    )


def test_model_breakdown_carries_last_used_date_per_model() -> None:
    """`last_used` is the most-recent date the model appeared in
    the window. The Models view shows it as a column so the user
    spots stale model usage at a glance."""
    bd = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=10, outputTokens=10,
        cacheCreationTokens=10, cacheReadTokens=10,
        cost=1.0,
    )
    report = _report([
        _entry("2026-05-12", model_breakdowns=[bd], total_cost=1.0),
        _entry("2026-05-15", model_breakdowns=[bd], total_cost=1.0),
    ])
    rows = model_breakdown(report)
    assert rows[0]["last_used"] == "2026-05-15"


def test_model_breakdown_per_kind_token_counts_sum_to_total() -> None:
    """Per-kind columns (input / output / cache_create / cache_read)
    drive the per-model token-kind chart. Their sum must equal the
    aggregate `tokens` field — keeps the chart's row width
    consistent with the table's Tokens column."""
    bd = ModelBreakdown(
        modelName="claude-opus-4-7",
        inputTokens=11, outputTokens=22,
        cacheCreationTokens=33, cacheReadTokens=44,
        cost=1.0,
    )
    report = _report(
        [_entry("2026-05-15", model_breakdowns=[bd], total_cost=1.0)]
    )
    rows = model_breakdown(report)
    row = rows[0]
    assert row["input"] + row["output"] + row["cache_create"] + row[
        "cache_read"
    ] == row["tokens"]


# ---------- cost_concentration_summary ----------


def test_cost_concentration_summary_picks_top_cost_row() -> None:
    """The KPI card's `Top model` carries the highest-cost row's
    name + family + share."""
    rows = [
        {"model": "claude-opus-4-7", "family": "opus", "cost": 9.0, "share": 0.9},
        {"model": "claude-haiku-4-5", "family": "haiku", "cost": 1.0, "share": 0.1},
    ]
    summary = cost_concentration_summary(rows)
    assert summary == {
        "model": "claude-opus-4-7",
        "family": "opus",
        "share": 0.9,
    }


def test_cost_concentration_summary_empty_rows_returns_none() -> None:
    """Empty window → None so the KPI card renders its `—`
    fallback instead of crashing on `max(...)`."""
    assert cost_concentration_summary([]) is None

