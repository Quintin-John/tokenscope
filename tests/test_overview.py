"""Unit tests for tokenscope.ui.overview pure helpers.

End-to-end rendering of the Overview view is covered by
`test_ui_smoke.py`. This file holds focused tests on the pure
helpers in `overview.py` that don't need a full Streamlit runtime.

Today that's just `_window_days`. The helper is currently exercised
end-to-end via the rendered subtitle, but only on the happy path —
its malformed/None branches (lines 522-528) have no direct test.

An upcoming slice will move the YYYYMMDD parsing onto `Query`
itself (collapsing three independent parsers across `analytics.py`,
`overview.py`, and `cache.py` into one). Pinning the malformed-input
contract here means that refactor can be verified before/after with
the same assertion.
"""

from __future__ import annotations

import pytest

from tokenscope.query import Query
from tokenscope.ui.overview import _window_days


def test_window_days_returns_inclusive_length_for_valid_range() -> None:
    """Happy path: a 30-day inclusive window returns 30. The `+1`
    in the implementation matches the conventional "last 30 days"
    reading the sidebar uses (since == today - 29, until == today).
    """
    q = Query(since="20260417", until="20260516")
    assert _window_days(q) == 30


def test_window_days_single_day_returns_one() -> None:
    """A range covering a single day (since == until) returns 1,
    not 0 — the day is inclusive on both ends."""
    q = Query(since="20260516", until="20260516")
    assert _window_days(q) == 1


def test_window_days_returns_none_when_since_missing() -> None:
    """No `since` → no defined window → None. Caller (overview
    render) falls back to `config.DEFAULT_RANGE_DAYS`."""
    assert _window_days(Query(until="20260516")) is None


def test_window_days_returns_none_when_until_missing() -> None:
    """Same contract for the `until`-missing branch."""
    assert _window_days(Query(since="20260417")) is None


def test_window_days_returns_none_when_both_bounds_missing() -> None:
    """A bare `Query()` (no filters) yields no window length."""
    assert _window_days(Query()) is None


@pytest.mark.parametrize(
    "bad_since,bad_until",
    [
        ("not-a-date", "20260516"),
        ("20260417", "not-a-date"),
        ("2026-04-17", "20260516"),   # ISO form, not the ccusage YYYYMMDD
        ("20260417", "20260516XYZ"),  # trailing junk
        ("", "20260516"),             # empty string (truthy guard skipped)
    ],
)
def test_window_days_returns_none_on_malformed_date(
    bad_since: str, bad_until: str
) -> None:
    """Malformed `since` / `until` → None rather than ValueError
    leaking out of the render path. The sidebar always formats
    valid YYYYMMDD, but a forged URL or a forward-compat schema
    change could leak something else through — the helper has to
    fail closed.

    Note: the empty-string case exercises the truthy `if not q.since`
    guard at the top; the ValueError branch fires for the other four.
    """
    q = Query(since=bad_since, until=bad_until)
    assert _window_days(q) is None
