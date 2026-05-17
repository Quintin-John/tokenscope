"""Unit tests for `tokenscope.ui.cache` pure helpers.

The Cache view's render functions can only be exercised through
`AppTest` (they call `st.markdown` / `st.plotly_chart` and need a
Streamlit runtime) — those live in `test_ui_smoke.py`.

This file holds the pure helpers that don't need a runtime, so
they get standard unit-test coverage with synthetic inputs.
"""

from __future__ import annotations

from datetime import date

from tokenscope.models import DailyEntry, DailyReport, ModelBreakdown, Totals
from tokenscope.ui.cache import _parse_ccusage_date, _render_data_range_banner


def _report_with_cache_on(*dates: str) -> DailyReport:
    """Build a minimal `DailyReport` with one cache-bearing entry per
    date supplied. Used to drive `_render_data_range_banner` past
    the early-return branches."""
    entries = [
        DailyEntry(
            date=d,
            inputTokens=100, outputTokens=200,
            cacheCreationTokens=300, cacheReadTokens=400,
            totalTokens=1000, totalCost=1.0,
            modelsUsed=["claude-opus-4-7"],
            modelBreakdowns=[
                ModelBreakdown(
                    modelName="claude-opus-4-7",
                    inputTokens=100, outputTokens=200,
                    cacheCreationTokens=300, cacheReadTokens=400,
                    cost=1.0,
                )
            ],
        )
        for d in dates
    ]
    return DailyReport(
        daily=entries,
        totals=Totals(
            inputTokens=100 * len(entries),
            outputTokens=200 * len(entries),
            cacheCreationTokens=300 * len(entries),
            cacheReadTokens=400 * len(entries),
            totalTokens=1000 * len(entries),
            totalCost=1.0 * len(entries),
        ),
    )


def test_render_data_range_banner_returns_early_when_since_is_none() -> None:
    """When the sidebar's `since` is `None` (no `--since` set,
    pure baseline), the banner short-circuits before touching the
    Streamlit API. Unit-testable because the early-return path
    never calls `st.markdown`."""
    report = _report_with_cache_on("2026-05-15")
    # No exception → the early return fired and Streamlit was
    # never reached. If the function fell through, it would crash
    # on `st.markdown` without a Streamlit runtime in this test.
    _render_data_range_banner(report, sidebar_since=None)


def test_render_data_range_banner_returns_early_when_since_unparseable() -> None:
    """A malformed `since` value (length wrong, garbage text)
    returns from the banner just like a `None` would. Same
    defensive contract."""
    report = _report_with_cache_on("2026-05-15")
    _render_data_range_banner(report, sidebar_since="not-a-date")


def test_render_data_range_banner_returns_early_when_no_cache_data() -> None:
    """Sidebar has `since`, but the report has zero cache
    activity → `cache_data_range` returns None → banner is
    suppressed. The Cache view's empty-window info banner is the
    user-visible signal in this case, not the range banner."""
    no_cache_report = DailyReport(
        daily=[
            DailyEntry(
                date="2026-05-15",
                inputTokens=100, outputTokens=200,
                cacheCreationTokens=0, cacheReadTokens=0,
                totalTokens=300, totalCost=1.0,
                modelsUsed=["claude-opus-4-7"],
                modelBreakdowns=[
                    ModelBreakdown(
                        modelName="claude-opus-4-7",
                        inputTokens=100, outputTokens=200,
                        cacheCreationTokens=0, cacheReadTokens=0,
                        cost=1.0,
                    )
                ],
            )
        ],
        totals=Totals(
            inputTokens=100, outputTokens=200,
            cacheCreationTokens=0, cacheReadTokens=0,
            totalTokens=300, totalCost=1.0,
        ),
    )
    _render_data_range_banner(no_cache_report, sidebar_since="20260418")


def test_render_data_range_banner_returns_early_when_data_covers_since() -> None:
    """When the cache data range starts ON or BEFORE the sidebar's
    `since`, the banner is suppressed — there's no gap to
    explain."""
    report = _report_with_cache_on("2026-05-15")
    # sidebar_since (May 15) == first_with_cache (May 15) → suppress.
    _render_data_range_banner(report, sidebar_since="20260515")
    # sidebar_since AFTER first_with_cache → still suppressed
    # (the comparison is `first_with_cache <= since_date`).
    _render_data_range_banner(report, sidebar_since="20260520")


def test_parse_ccusage_date_yyyymmdd_form() -> None:
    """The sidebar formats `state.query.since` as compact
    `YYYYMMDD` (ccusage's expected `--since=` format). The Cache
    view parses that back into a `date` so the banner can compare
    against `cache_data_range`'s `YYYY-MM-DD` strings."""
    assert _parse_ccusage_date("20260418") == date(2026, 4, 18)


def test_parse_ccusage_date_iso_fallback() -> None:
    """A `YYYY-MM-DD` string (e.g. if a future caller passes the
    ISO form directly) parses via the secondary path. Same
    return type — keeps the banner agnostic to which format the
    sidebar happens to use."""
    assert _parse_ccusage_date("2026-04-18") == date(2026, 4, 18)


def test_parse_ccusage_date_none_returns_none() -> None:
    """A `None` input (no `since` in the URL) returns `None` so
    the banner defensively suppresses itself rather than crashing."""
    assert _parse_ccusage_date(None) is None


def test_parse_ccusage_date_empty_string_returns_none() -> None:
    """Empty string is the same signal as `None` — no `since` was
    set in the URL. Banner is suppressed."""
    assert _parse_ccusage_date("") is None


def test_parse_ccusage_date_malformed_yyyymmdd_returns_none() -> None:
    """An 8-digit input that doesn't form a valid date (month 13,
    day 32, etc.) returns None rather than raising. URL query
    params can be anything a user pastes; the banner shouldn't
    crash the whole Cache view on a malformed date."""
    assert _parse_ccusage_date("20261332") is None


def test_parse_ccusage_date_garbage_returns_none() -> None:
    """Non-date strings ('hello', 'abcdefgh') return None. Same
    defensive contract as the malformed-numeric case."""
    assert _parse_ccusage_date("hello") is None
    assert _parse_ccusage_date("abcdefgh") is None
