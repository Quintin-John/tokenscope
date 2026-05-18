"""Unit tests for `tokenscope.query.Query` — argv build + bound parsing.

Slice D moved YYYYMMDD parsing from three independent call sites
(`analytics.prior_window_query`, `overview._window_days`,
`cache._parse_ccusage_date`) onto `Query` itself as the methods
`since_date`, `until_date`, and `window_days`. Tests originally
written against `overview._window_days` (in the now-removed
`tests/test_overview.py`) and against `cache._parse_ccusage_date`
(in `tests/test_cache.py`) migrated here.

The ISO-fallback path that used to exist in `_parse_ccusage_date`
is intentionally NOT preserved on `Query`: every production input
to `Query.since` / `Query.until` is formatted by
`sidebar._to_ccusage_date(date) -> str` which always emits
8-digit `YYYYMMDD`, and URL `since` params are reformatted into
that shape before reaching `Query`. The ISO branch was dead code;
its test goes away with it (per persona "Don't add error handling,
fallbacks, or validation for scenarios that can't happen").
"""

from __future__ import annotations

from datetime import date

import pytest

from tokenscope.query import Query


# ---------- since_date / until_date ------------------------------------


def test_since_date_parses_yyyymmdd_form() -> None:
    """The sidebar formats `Query.since` as compact `YYYYMMDD`
    (ccusage's expected `--since=` format). `since_date()` parses
    that back into a `date` for downstream consumers."""
    assert Query(since="20260418").since_date() == date(2026, 4, 18)


def test_until_date_parses_yyyymmdd_form() -> None:
    """Symmetric contract for the `until` bound."""
    assert Query(until="20260516").until_date() == date(2026, 5, 16)


def test_since_date_returns_none_when_unset() -> None:
    """A bare `Query()` (no filters set) returns None for both
    bounds — callers fall back to whatever default the consuming
    view uses (`config.DEFAULT_RANGE_DAYS` for Overview, banner
    suppression for Cache)."""
    assert Query().since_date() is None
    assert Query().until_date() is None


def test_since_date_returns_none_for_empty_string() -> None:
    """Empty string is the same signal as `None` — no `since` was
    set. Callers treat both identically."""
    assert Query(since="").since_date() is None
    assert Query(until="").until_date() is None


@pytest.mark.parametrize(
    "bad_value",
    [
        "20261332",                # 8-digit but invalid date (month 13)
        "20260230",                # 8-digit but invalid date (Feb 30)
        "hello",                   # garbage
        "abcdefgh",                # 8-char garbage
        "2026-04-18",              # ISO form — strict YYYYMMDD only,
                                   # ISO fallback was unreachable dead
                                   # code in cache._parse_ccusage_date
        "20260418XYZ",             # trailing junk
        "2026041",                 # 7 digits (wrong length)
        "202604181",               # 9 digits (wrong length)
    ],
)
def test_since_date_returns_none_on_malformed_input(bad_value: str) -> None:
    """Strict YYYYMMDD — anything else returns None rather than
    raising. URL query params can be anything a user pastes; views
    fall back to their default window rather than crashing."""
    assert Query(since=bad_value).since_date() is None


@pytest.mark.parametrize(
    "bad_value",
    [
        "20261332",
        "20260230",
        "hello",
        "2026-04-18",
        "20260418XYZ",
    ],
)
def test_until_date_returns_none_on_malformed_input(bad_value: str) -> None:
    """Symmetric malformed-input contract for `until_date`. Smaller
    parametrize set — the input shape contract is identical to
    `since_date`, so we just sample a few cases to confirm symmetry."""
    assert Query(until=bad_value).until_date() is None


# ---------- window_days ------------------------------------------------


def test_window_days_returns_inclusive_length_for_valid_range() -> None:
    """A 30-day inclusive window (since == today - 29, until == today)
    returns 30. The `+1` matches the conventional "last 30 days"
    reading the sidebar uses."""
    assert Query(since="20260417", until="20260516").window_days() == 30


def test_window_days_single_day_returns_one() -> None:
    """A range covering a single day (since == until) returns 1,
    not 0 — the day is inclusive on both ends."""
    assert Query(since="20260516", until="20260516").window_days() == 1


def test_window_days_returns_none_when_since_missing() -> None:
    """No `since` → no defined window → None. Caller (overview
    render) falls back to `config.DEFAULT_RANGE_DAYS`."""
    assert Query(until="20260516").window_days() is None


def test_window_days_returns_none_when_until_missing() -> None:
    """Same contract for the `until`-missing branch."""
    assert Query(since="20260417").window_days() is None


def test_window_days_returns_none_when_both_bounds_missing() -> None:
    """A bare `Query()` (no filters) yields no window length."""
    assert Query().window_days() is None


@pytest.mark.parametrize(
    "bad_since,bad_until",
    [
        ("not-a-date", "20260516"),
        ("20260417", "not-a-date"),
        ("2026-04-17", "20260516"),    # ISO form, not ccusage YYYYMMDD
        ("20260417", "20260516XYZ"),   # trailing junk
        ("", "20260516"),              # empty string (falsy guard)
        ("20260417", ""),
    ],
)
def test_window_days_returns_none_on_malformed_date(
    bad_since: str, bad_until: str
) -> None:
    """Malformed `since` / `until` → None rather than ValueError
    leaking out of the render path. The sidebar always formats
    valid YYYYMMDD, but a forged URL or a forward-compat schema
    change could leak something else through — the method has to
    fail closed."""
    assert Query(since=bad_since, until=bad_until).window_days() is None


# ---------- Slice D composition invariants ------------------------------


def test_window_days_composes_correctly_from_since_and_until() -> None:
    """Slice D composition contract: `window_days()` must equal
    `(until_date() - since_date()).days + 1` whenever both bounds
    parse. A regression that diverged the three methods' parsing
    would surface here as a mismatch."""
    q = Query(since="20260301", until="20260331")
    since = q.since_date()
    until = q.until_date()
    assert since is not None
    assert until is not None
    assert q.window_days() == (until - since).days + 1


def test_since_and_until_date_use_same_parser() -> None:
    """Slice D consolidation invariant: a given YYYYMMDD string
    parses identically whether placed in `since` or `until`. If a
    future change split the parsers, this catches the asymmetry."""
    same_string = "20260418"
    assert (
        Query(since=same_string).since_date()
        == Query(until=same_string).until_date()
        == date(2026, 4, 18)
    )
