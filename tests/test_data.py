"""Unit tests for tokenscope.data and Query.argv.

Slice 4 collapsed the 16-fold wrapper boilerplate in `data.py` into a
single cached `_raw` transport function plus eight thin model-validate
public wrappers. These tests prove:

1. Each public function calls ccusage with the correct argv prefix
   (subcommand + optional --active / --instances + query args).
2. The cache key includes the subcommand — `data.daily()` and
   `data.weekly()` don't share a slot.
3. The empty-range coercion is applied for daily / weekly / monthly /
   session / *_by_project and skipped for blocks (whose JSON shape is
   already stable).
4. `Query.argv()` collapses what used to be the duplicate `_q` /
   `_q_args` one-line helpers; `Query.argv(None) == []`.

All tests are deterministic — they intercept `_run_json` via the
`mock_ccusage` fixture and never shell out to a real ccusage.
"""

from __future__ import annotations

from typing import Any

import pytest

from tokenscope import data
from tokenscope.models import (
    BlocksReport,
    DailyByProjectReport,
    DailyReport,
    MonthlyByProjectReport,
    MonthlyReport,
    SessionReport,
    WeeklyByProjectReport,
    WeeklyReport,
)
from tokenscope.query import Query


# ---------- Query.argv ----------


def test_query_argv_none_returns_empty_list() -> None:
    assert Query.argv(None) == []


def test_query_argv_bare_query_returns_empty_list() -> None:
    """A bare ``Query()`` has every field at its default of None — no
    flags should be emitted. Same effective behaviour as passing None,
    different code path."""
    assert Query.argv(Query()) == []


def test_query_argv_emits_joined_key_value_flags() -> None:
    """Project ids start with '-' (slugified absolute paths). The
    `--key=value` joined form prevents ccusage's parser from treating
    the value as the next flag. Regression for the bug that lost data
    on project filters with leading dashes."""
    argv = Query.argv(
        Query(
            since="20260401",
            until="20260516",
            project="-Users-foo-bar",
            offline=True,
            tz="America/New_York",
        )
    )
    assert argv == [
        "--since=20260401",
        "--until=20260516",
        "--project=-Users-foo-bar",
        "--timezone=America/New_York",
        "--offline",
    ]


# ---------- spy fixture for argv capture ----------


@pytest.fixture
def spy_ccusage(monkeypatch):
    """Capture every argv list passed to ccusage._run_json plus the
    response we hand back. Lets tests assert the EXACT argv composed
    by the data layer.

    Streamlit's cache_data is cleared at fixture setup so the assertion
    about which calls reach ccusage isn't polluted by leftover cache
    entries from earlier tests in the same process.
    """
    calls: list[list[str]] = []

    response_for_subcommand: dict[str, Any] = {}

    def fake_run_json(args: list[str]) -> Any:
        calls.append(list(args))
        sub = args[0] if args else ""
        return response_for_subcommand.get(sub, _empty_for(args))

    monkeypatch.setattr("tokenscope.ccusage._run_json", fake_run_json)
    import streamlit as st
    st.cache_data.clear()
    return calls, response_for_subcommand


def _empty_for(args: list[str]) -> Any:
    """Stable empty-shape per subcommand, mirroring what ccusage actually
    returns in production (so the coercion path doesn't unexpectedly
    fire on these tests)."""
    sub = args[0] if args else ""
    if "--instances" in args:
        return {"projects": {}, "totals": _TOTALS}
    if sub in ("daily", "weekly", "monthly"):
        return {sub: [], "totals": _TOTALS}
    if sub == "session":
        return {"sessions": [], "totals": _TOTALS}
    if sub == "blocks":
        return {"blocks": []}
    raise AssertionError(f"unexpected subcommand: {args!r}")


_TOTALS = {
    "inputTokens": 0,
    "outputTokens": 0,
    "cacheCreationTokens": 0,
    "cacheReadTokens": 0,
    "totalTokens": 0,
    "totalCost": 0,
}


# ---------- public layer: argv composition + model type ----------


def test_daily_calls_daily_subcommand_returns_daily_report(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    report = data.daily()
    assert isinstance(report, DailyReport)
    assert calls == [["daily"]]


def test_daily_includes_query_args_in_argv(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    data.daily(Query(since="20260401", until="20260516", offline=True))
    assert calls == [
        ["daily", "--since=20260401", "--until=20260516", "--offline"]
    ]


def test_weekly_calls_weekly_subcommand(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    assert isinstance(data.weekly(), WeeklyReport)
    assert calls == [["weekly"]]


def test_monthly_calls_monthly_subcommand(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    assert isinstance(data.monthly(), MonthlyReport)
    assert calls == [["monthly"]]


def test_session_calls_session_subcommand(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    assert isinstance(data.session(), SessionReport)
    assert calls == [["session"]]


def test_blocks_default_omits_active_flag(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    assert isinstance(data.blocks(), BlocksReport)
    assert calls == [["blocks"]]


def test_blocks_active_true_passes_active_flag(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    data.blocks(active=True)
    assert calls == [["blocks", "--active"]]


def test_blocks_active_combined_with_query(spy_ccusage) -> None:
    """--active must come BEFORE the query args (order matters for
    ccusage's flag parser — leading-dash project values break under
    space-separated arg parsing, hence the --key=value form from
    Query.to_args)."""
    calls, _ = spy_ccusage
    data.blocks(active=True, query=Query(since="20260401"))
    assert calls == [["blocks", "--active", "--since=20260401"]]


def test_daily_by_project_adds_instances_flag(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    assert isinstance(data.daily_by_project(), DailyByProjectReport)
    assert calls == [["daily", "--instances"]]


def test_weekly_by_project_adds_instances_flag(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    assert isinstance(data.weekly_by_project(), WeeklyByProjectReport)
    assert calls == [["weekly", "--instances"]]


def test_monthly_by_project_adds_instances_flag(spy_ccusage) -> None:
    calls, _ = spy_ccusage
    assert isinstance(data.monthly_by_project(), MonthlyByProjectReport)
    assert calls == [["monthly", "--instances"]]


# ---------- cache-key isolation ----------


def test_cache_distinguishes_subcommands(spy_ccusage) -> None:
    """Pre-refactor, each subcommand had its own cached `_*_raw` function,
    so cache slots were inherently distinct. Post-refactor a single
    `_raw` is shared — the subcommand must therefore appear in the
    cache key, otherwise daily and weekly would collide.

    Two distinct subcommands → two distinct ccusage calls.
    """
    calls, _ = spy_ccusage
    data.daily()
    data.weekly()
    assert calls == [["daily"], ["weekly"]]


def test_cache_distinguishes_active_flag(spy_ccusage) -> None:
    """`active=True` and `active=False` must map to different cache
    slots — they hit different argv and could return different data."""
    calls, _ = spy_ccusage
    data.blocks(active=False)
    data.blocks(active=True)
    assert calls == [["blocks"], ["blocks", "--active"]]


def test_cache_distinguishes_project_view(spy_ccusage) -> None:
    """`daily` and `daily_by_project` share a subcommand but differ in
    `project_view=True` — must not share a cache slot."""
    calls, _ = spy_ccusage
    data.daily()
    data.daily_by_project()
    assert calls == [["daily"], ["daily", "--instances"]]


def test_cache_distinguishes_query(spy_ccusage) -> None:
    """Different Query instances → different cache keys."""
    calls, _ = spy_ccusage
    data.daily(Query(since="20260401"))
    data.daily(Query(since="20260402"))
    assert calls == [
        ["daily", "--since=20260401"],
        ["daily", "--since=20260402"],
    ]


def test_cache_hit_skips_ccusage_call(spy_ccusage) -> None:
    """Within the cache TTL, a repeat call with the same argv must not
    re-shell to ccusage. Streamlit's @st.cache_data does the work; the
    test verifies our refactor preserved the cache decorator on
    `_raw`."""
    calls, _ = spy_ccusage
    data.daily(Query(since="20260401"))
    data.daily(Query(since="20260401"))
    assert calls == [["daily", "--since=20260401"]]


# ---------- empty-range coercion ----------


def test_daily_coerces_bare_empty_list_to_canonical_shape(monkeypatch) -> None:
    """ccusage returns a bare `[]` for empty windows. `_coerce_empty`
    wraps that into ``{"daily": [], "totals": {...}}`` so pydantic
    validation doesn't crash with a misleading "expected dict" error.
    Slice 4 must preserve this through the refactor."""

    def fake_run_json(_args: list[str]) -> Any:
        return []  # the misbehaving ccusage shape

    monkeypatch.setattr("tokenscope.ccusage._run_json", fake_run_json)
    import streamlit as st
    st.cache_data.clear()

    report = data.daily()
    assert isinstance(report, DailyReport)
    assert report.daily == []
    assert report.totals.total_cost == 0


def test_daily_by_project_coerces_with_dict_container(monkeypatch) -> None:
    """The `projects` key in *_by_project reports holds a dict (project
    name → entries), not a list. The coercion path must respect that
    when filling in the empty shape."""

    def fake_run_json(_args: list[str]) -> Any:
        return []

    monkeypatch.setattr("tokenscope.ccusage._run_json", fake_run_json)
    import streamlit as st
    st.cache_data.clear()

    report = data.daily_by_project()
    assert isinstance(report, DailyByProjectReport)
    assert report.projects == {}


def test_blocks_skips_coercion_path(monkeypatch) -> None:
    """`blocks` JSON shape is stable across empty/non-empty results, so
    `_raw` is called with `coerce_key=None` and skips `_coerce_empty`.
    Verifying the empty `{"blocks": []}` flows through directly."""

    def fake_run_json(_args: list[str]) -> Any:
        return {"blocks": []}

    monkeypatch.setattr("tokenscope.ccusage._run_json", fake_run_json)
    import streamlit as st
    st.cache_data.clear()

    report = data.blocks()
    assert isinstance(report, BlocksReport)
    assert report.blocks == []
