"""Parse ccusage fixture JSON into pydantic models.

These tests don't shell out to ccusage — they validate captured JSON fixtures.
Live ccusage calls live in `test_ccusage_live.py` behind `@pytest.mark.integration`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenscope.models import (
    BlocksReport,
    DailyByProjectReport,
    DailyReport,
    MonthlyReport,
    SessionReport,
    WeeklyReport,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_daily_parses() -> None:
    report = DailyReport.model_validate(_load("daily.json"))
    assert len(report.daily) > 0
    entry = report.daily[0]
    assert entry.date
    assert entry.total_cost >= 0
    assert entry.total_tokens >= 0
    breakdown_cost = sum(b.cost for b in entry.model_breakdowns)
    assert breakdown_cost == pytest.approx(entry.total_cost, rel=1e-6)


def test_daily_token_totals_consistent() -> None:
    report = DailyReport.model_validate(_load("daily.json"))
    for entry in report.daily:
        computed = (
            entry.input_tokens
            + entry.output_tokens
            + entry.cache_creation_tokens
            + entry.cache_read_tokens
        )
        assert computed == entry.total_tokens, f"{entry.date}: {computed} != {entry.total_tokens}"


def test_daily_top_level_totals_match_entries() -> None:
    """The report-level `totals` summary should equal the sum of entry tokens."""
    report = DailyReport.model_validate(_load("daily.json"))
    summed_input = sum(e.input_tokens for e in report.daily)
    summed_output = sum(e.output_tokens for e in report.daily)
    summed_cache_create = sum(e.cache_creation_tokens for e in report.daily)
    summed_cache_read = sum(e.cache_read_tokens for e in report.daily)
    summed_cost = sum(e.total_cost for e in report.daily)
    assert summed_input == report.totals.input_tokens
    assert summed_output == report.totals.output_tokens
    assert summed_cache_create == report.totals.cache_creation_tokens
    assert summed_cache_read == report.totals.cache_read_tokens
    assert summed_cost == pytest.approx(report.totals.total_cost, rel=1e-6)


def test_weekly_parses() -> None:
    report = WeeklyReport.model_validate(_load("weekly.json"))
    assert len(report.weekly) > 0
    assert report.weekly[0].week


def test_monthly_parses() -> None:
    report = MonthlyReport.model_validate(_load("monthly.json"))
    assert len(report.monthly) > 0
    assert report.monthly[0].month


def test_session_parses() -> None:
    report = SessionReport.model_validate(_load("session.json"))
    assert len(report.sessions) > 0
    s = report.sessions[0]
    assert s.session_id
    assert s.last_activity


def test_daily_with_project_filter_parses() -> None:
    """Regression: `ccusage daily --project=<id>` adds a `project` field per
    entry. We allow it as optional rather than tripping extra="forbid"."""
    report = DailyReport.model_validate(_load("daily_with_project.json"))
    assert report.daily
    for entry in report.daily:
        assert entry.project  # populated when --project is used
        assert entry.project.startswith("-")  # ccusage's slugged-path ids


def test_daily_by_project_parses() -> None:
    report = DailyByProjectReport.model_validate(_load("daily_by_project.json"))
    assert report.projects
    # At least one project has entries.
    for project_id, entries in report.projects.items():
        assert project_id
        for entry in entries:
            assert entry.date
            assert entry.total_cost >= 0
    # Top-level totals match the sum across all projects' entries.
    summed = sum(
        e.total_cost for entries in report.projects.values() for e in entries
    )
    assert summed == pytest.approx(report.totals.total_cost, rel=1e-6)


def test_blocks_parses() -> None:
    report = BlocksReport.model_validate(_load("blocks.json"))
    assert len(report.blocks) > 0
    for block in report.blocks:
        assert block.id
        if block.is_gap:
            assert block.burn_rate is None
            assert block.actual_end_time is None
        else:
            assert block.entries >= 0
