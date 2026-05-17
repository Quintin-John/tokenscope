"""Tests for the Live view — timezone correctness + throughput
chart gating + performance budget.

Covers the regression set the user reported on the deployed app:

  * Charts rendering UTC ticks instead of the user's wall-clock time
    (`19:00–23:30` on an Eastern-time user whose banner said 15:00).
  * Throughput chart emitting four identical X-axis ticks
    (`22:41 · 22:41 · 22:41 · 22:41`) on a block too new to have
    accumulated more than one cumulative-snapshot bucket.
  * Throughput chart's X-axis collapsing to the elapsed portion
    instead of spanning the full 5-hour window.
  * Throughput chart lagging the rest of the page on each 30s
    refresh.

Tests run two layers:

  * Pure chart-builder unit tests — call `live_spend_trajectory`
    / `live_token_throughput` directly with `tz=...` and assert
    on the figure spec. Fast, deterministic.
  * AppTest smoke tests — boot the full app with a stubbed
    ccusage + stubbed sidebar tz detection, render the Live
    page, walk the figure spec or markdown.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import FIXTURES
from tokenscope.models import (
    BlockEntry,
    BlockTokenCounts,
    BurnRate,
    Projection,
)
from tokenscope.ui.charts import (
    live_spend_trajectory,
    live_token_kind_composition_bar,
)


APP_PATH = "src/tokenscope/app.py"


# --- tz helpers ---------------------------------------------------------


def test_utc_iso_to_local_naive_iso_strips_z_marker() -> None:
    """Standard happy path — EDT (UTC-4) conversion drops the
    trailing `Z` and shifts the clock by 4 hours."""
    from tokenscope.tz import utc_iso_to_local_naive_iso

    assert (
        utc_iso_to_local_naive_iso(
            "2026-05-17T19:00:00.000Z", "America/New_York"
        )
        == "2026-05-17T15:00:00"
    )


def test_utc_iso_to_local_naive_iso_empty_returns_none() -> None:
    """Empty / `None` input → `None`. Defensive: caller decides
    whether to fall back to a UTC string or hide the element."""
    from tokenscope.tz import utc_iso_to_local_naive_iso

    assert utc_iso_to_local_naive_iso("", "America/New_York") is None


def test_utc_iso_to_local_naive_iso_malformed_returns_none() -> None:
    """A non-ISO string fails the `fromisoformat` parse and returns
    `None` so the chart layer falls back to the raw input rather
    than crashing on a Plotly build error."""
    from tokenscope.tz import utc_iso_to_local_naive_iso

    assert utc_iso_to_local_naive_iso("not-a-date", "America/New_York") is None


def test_utc_iso_to_local_naive_iso_unknown_zone_returns_input() -> None:
    """Unknown / malformed IANA zone → return the raw input. The
    chart layer treats this as 'fell back to UTC' rather than
    crashing the page."""
    from tokenscope.tz import utc_iso_to_local_naive_iso

    raw = "2026-05-17T19:00:00.000Z"
    assert (
        utc_iso_to_local_naive_iso(raw, "Not/A_Real_Zone")
        == raw
    )


def test_minutes_since_utc_iso_basic_delta() -> None:
    """7-minute elapsed against a frozen `now`."""
    from tokenscope.tz import minutes_since_utc_iso

    now = datetime(2026, 5, 17, 19, 7, tzinfo=timezone.utc)
    assert minutes_since_utc_iso("2026-05-17T19:00:00.000Z", now) == 7.0


def test_minutes_since_utc_iso_empty_returns_none() -> None:
    """Empty input → None. Same defensive contract as the other
    helpers."""
    from tokenscope.tz import minutes_since_utc_iso

    assert minutes_since_utc_iso("") is None


def test_minutes_since_utc_iso_malformed_returns_none() -> None:
    """Malformed timestamp → None rather than ValueError leaking
    out into the Live render path."""
    from tokenscope.tz import minutes_since_utc_iso

    assert minutes_since_utc_iso("not-a-date") is None


# --- fixture-equivalent block ------------------------------------------
#
# Active block from `tests/fixtures/blocks.json`: starts at 13:00 UTC
# (09:00 EDT on 2026-05-16, which is summer time). Mirroring those
# exact timestamps here keeps the chart-builder unit tests and the
# AppTest smoke tests aligned.


def _active_block(
    *,
    start: str = "2026-05-16T13:00:00.000Z",
    end: str = "2026-05-16T18:00:00.000Z",
) -> BlockEntry:
    return BlockEntry(
        id=start,
        startTime=start,
        endTime=end,
        actualEndTime=None,
        isActive=True,
        isGap=False,
        entries=10,
        tokenCounts=BlockTokenCounts(
            inputTokens=100, outputTokens=200,
            cacheCreationInputTokens=300, cacheReadInputTokens=400,
        ),
        totalTokens=1000,
        costUSD=1.0,
        models=["claude-opus-4-7"],
        burnRate=BurnRate(
            tokensPerMinute=1.0,
            tokensPerMinuteForIndicator=1.0,
            costPerHour=2.0,
        ),
        projection=Projection(
            totalTokens=10000, totalCost=10.0, remainingMinutes=180,
        ),
    )


# --- Bug 1: charts must use local timezone, not UTC ---------------------


def test_live_charts_x_axis_in_local_timezone() -> None:
    """The user's auto-detected zone (America/New_York at the time of
    the bug report — EDT, UTC-4) was being silently ignored: the
    spend chart's X-axis ticks rendered in UTC (`19:00 → 23:30`
    instead of `15:00 → 19:30`).

    Regression contract: when the caller passes ``tz``, every
    X-axis value reaching Plotly is in naive local-clock ISO form
    (no trailing `Z`, no `+offset`) — Plotly treats those as
    wall-clock instants and ticks at the same numerals."""
    block = _active_block()
    fig = live_spend_trajectory(
        block,
        samples=[],
        now_iso="2026-05-16T15:30:00Z",
        tz="America/New_York",
    )
    actual = next(t for t in fig.data if t.name == "Actual")
    # Block start 13:00 UTC → 09:00 EDT on a May date (DST).
    assert list(actual.x)[0] == "2026-05-16T09:00:00", (
        f"first x value should be local-clock 09:00, got {actual.x[0]!r}"
    )
    # `now_iso` 15:30 UTC → 11:30 EDT.
    assert list(actual.x)[-1] == "2026-05-16T11:30:00", (
        f"last x value should be local-clock 11:30, got {actual.x[-1]!r}"
    )


def test_live_spend_trajectory_x_axis_range_spans_full_block_window() -> None:
    """The X-axis range is pinned to the block's full 5-hour window
    in local-clock time. Without this, Plotly auto-scales to the
    data range, which on a new block collapses to a near-zero
    width (the regression that produced four identical `22:41`
    ticks on the user's screenshot)."""
    block = _active_block()
    fig = live_spend_trajectory(
        block, samples=[], now_iso="2026-05-16T13:30:00Z",
        tz="America/New_York",
    )
    xrange = fig.layout.xaxis.range
    assert xrange[0] == "2026-05-16T09:00:00"
    assert xrange[1] == "2026-05-16T14:00:00"


def test_live_token_kind_composition_bar_builds_quickly() -> None:
    """The honest composition bar replaces the prior throughput
    time-series. There is no fetch, no bucketing, no sample
    history — just four `go.Bar` traces built from one
    `block.token_counts` dict. Server-side build must be
    well under the 200ms budget the user named for this
    surface."""
    block = _active_block()
    start = time.perf_counter()
    fig = live_token_kind_composition_bar(block)
    elapsed = time.perf_counter() - start
    assert fig is not None
    assert elapsed < 0.2, (
        f"composition bar build exceeded 200ms budget: {elapsed * 1000:.1f}ms"
    )


def test_live_spend_trajectory_now_reference_is_localized() -> None:
    """The vertical `now` reference line uses the same local-naive
    ISO as the trace data — drift between the two would put the
    line at the wrong horizontal position relative to the trace."""
    block = _active_block()
    fig = live_spend_trajectory(
        block, samples=[],
        now_iso="2026-05-16T15:30:00Z",
        tz="America/New_York",
    )
    now_lines = [
        s for s in fig.layout.shapes
        if s.type == "line" and s.x0 == s.x1
    ]
    assert len(now_lines) == 1
    assert now_lines[0].x0 == "2026-05-16T11:30:00"


def test_live_charts_fall_back_to_utc_when_tz_none() -> None:
    """Defensive: when no IANA zone is available (e.g. fully
    sandboxed test environment with no TZ env var and no
    `/etc/localtime` symlink), the charts must still render —
    just with UTC ticks. Passing `tz=None` returns the raw ISO
    unchanged, which Plotly treats as UTC by convention."""
    block = _active_block()
    fig = live_spend_trajectory(
        block, samples=[], now_iso="2026-05-16T15:30:00Z", tz=None
    )
    actual = next(t for t in fig.data if t.name == "Actual")
    # Raw UTC ISO from the block fixture (trailing `.000Z` retained).
    assert list(actual.x)[0] == "2026-05-16T13:00:00.000Z"


# --- helpers + Live-page smoke ------------------------------------------


def _wire_live_fixtures(mock_ccusage, blocks_payload: dict) -> None:
    """Wire the minimum ccusage responses needed for the Live page
    to render against `blocks_payload`. The Live view also pulls
    daily / session for the sidebar, so we re-use the standard
    fixtures for those (they don't affect the throughput chart's
    gating logic)."""
    mock_ccusage("daily", response=FIXTURES / "daily.json")
    mock_ccusage(
        "daily", "--instances", response=FIXTURES / "daily_by_project.json"
    )
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=blocks_payload)
    mock_ccusage("blocks", "--active", response=blocks_payload)


def _make_active_block_payload(
    start_iso: str, end_iso: str | None = None
) -> dict:
    """Single-block `blocks --json` payload with the active block
    starting at ``start_iso``. End defaults to start + 5h so the
    standard 5-hour window invariant holds."""
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end_dt = (
        datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        if end_iso
        else None
    )
    if end_dt is None:
        from datetime import timedelta as _td

        end_dt = start_dt + _td(hours=5)
    return {
        "blocks": [
            {
                "id": start_iso,
                "startTime": start_iso,
                "endTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "actualEndTime": None,
                "isActive": True,
                "isGap": False,
                "entries": 10,
                "tokenCounts": {
                    "inputTokens": 100, "outputTokens": 200,
                    "cacheCreationInputTokens": 300,
                    "cacheReadInputTokens": 400,
                },
                "totalTokens": 1000,
                "costUSD": 1.0,
                "models": ["claude-opus-4-7"],
                "burnRate": {
                    "tokensPerMinute": 1.0,
                    "tokensPerMinuteForIndicator": 1.0,
                    "costPerHour": 2.0,
                },
                "projection": {
                    "totalTokens": 10000,
                    "totalCost": 10.0,
                    "remainingMinutes": 180,
                },
            }
        ]
    }


def test_live_token_mix_chart_renders_on_live_view(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The composition bar is unconditional — no elapsed-time gate
    (the prior throughput chart's gate is gone because the
    composition snapshot is meaningful from the very first
    refresh: it's just `block.token_counts` rendered as four
    coloured segments). Locks the chart's presence on the Live
    view by key."""
    payload = _make_active_block_payload("2026-05-16T13:00:00.000Z")
    _wire_live_fixtures(mock_ccusage, payload)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    at.run()

    assert not at.exception, [str(e.value)[:200] for e in at.exception]
    chart_keys = _plotly_chart_keys(at)
    assert "live-token-mix" in chart_keys, (
        f"composition bar not rendered on Live view; keys: {chart_keys!r}"
    )


# --- perf budget --------------------------------------------------------


def test_live_page_renders_within_perf_budget(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Server-side render budget for the entire Live page. The user
    reported the prior token-throughput chart "lagging compared
    to the rest of the page"; with the composition bar (one
    `block.token_counts` dict, no fetch, no bucketing) the whole
    page should land well under 2s."""
    _wire_live_fixtures(
        mock_ccusage,
        _make_active_block_payload("2026-05-16T13:00:00.000Z"),
    )
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    start = time.perf_counter()
    at.run()
    elapsed = time.perf_counter() - start

    assert not at.exception, [str(e.value)[:200] for e in at.exception]
    assert elapsed < 2.0, (
        f"Live page render exceeded budget: {elapsed:.2f}s > 2.00s"
    )


# --- guardrail: no UTC strings in rendered Live HTML --------------------


def test_live_no_utc_strings_in_rendered_html(
    mock_ccusage, mock_ccusage_version, monkeypatch
) -> None:
    """End-to-end check that nothing on the rendered Live page
    leaks the UTC encoding back to the user — no trailing `Z`
    timestamps, no literal `UTC` label.

    The sidebar's `detect_local_iana()` defaults to UTC when no
    zone resolves; we patch it to return a real zone so the chart
    builders' localisation path is exercised."""
    monkeypatch.setattr(
        "tokenscope.tz.detect_local_iana",
        lambda: "America/New_York",
    )
    _wire_live_fixtures(
        mock_ccusage,
        _make_active_block_payload("2026-05-16T13:00:00.000Z"),
    )

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    at.run()

    assert not at.exception, [str(e.value)[:200] for e in at.exception]
    md = "\n".join(m.value for m in at.markdown)
    # No trailing `Z` UTC marker on any visible timestamp in the
    # markdown surface (the banner, the chart cards' captions,
    # the throughput empty-state panel, etc.).
    assert "Z<" not in md, "found `Z<` — likely a UTC timestamp leaked"
    # No literal `UTC` label — the auto-detected zone is what the
    # user sees in the banner and chart axes.
    assert "UTC" not in md, "Live page surfaces should not name UTC"

    # Walk the Plotly figure specs too — the chart axes are part
    # of the surface the user sees and a leaked Z would show up
    # there even if the markdown is clean.
    charts = _walk_plotly_figure_strings(at)
    utc_leaks = [s for s in charts if s.endswith("Z") and "T" in s]
    assert not utc_leaks, (
        f"chart figure specs contain UTC ISO leaks: {utc_leaks[:3]!r}"
    )


# --- helpers ------------------------------------------------------------


def _plotly_chart_keys(at: AppTest) -> set[str]:
    """Walk the AppTest element tree, return every Plotly chart's
    `key`. Matches the helper in `test_ui_smoke.py`."""
    keys: set[str] = set()

    def visit(node) -> None:
        if getattr(node, "type", None) == "plotly_chart":
            parts = node.proto.id.split("-", 2)
            if len(parts) == 3:
                keys.add(parts[2])
        children = getattr(node, "children", None)
        if children:
            iterator = (
                children.values() if hasattr(children, "values") else children
            )
            for child in iterator:
                visit(child)

    visit(at.main)
    return keys


def _walk_plotly_figure_strings(at: AppTest) -> list[str]:
    """Collect every string value across every rendered Plotly
    figure's spec. Used by the UTC-leak regression to surface a
    leaked `...Z` timestamp anywhere in the figure JSON, not just
    in trace data."""
    out: list[str] = []

    def visit_str(obj) -> None:
        if isinstance(obj, str):
            out.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                visit_str(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                visit_str(v)

    def visit(node) -> None:
        if getattr(node, "type", None) == "plotly_chart":
            try:
                spec = json.loads(node.proto.spec)
            except (ValueError, AttributeError):
                spec = None
            if spec:
                visit_str(spec)
        children = getattr(node, "children", None)
        if children:
            iterator = (
                children.values() if hasattr(children, "values") else children
            )
            for child in iterator:
                visit(child)

    visit(at.main)
    return out
