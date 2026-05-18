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
    start_iso: str,
    end_iso: str | None = None,
    *,
    input_tokens: int = 100,
    output_tokens: int = 200,
    cache_create_tokens: int = 300,
    cache_read_tokens: int = 400,
) -> dict:
    """Single-block `blocks --json` payload with the active block
    starting at ``start_iso``. End defaults to start + 5h so the
    standard 5-hour window invariant holds.

    Token counts default to the legacy 100/200/300/400 set the
    existing tests are pinned to. Callers that need distinct values
    per kind (e.g. the BlockTokenCounts→kind mapping regression
    tests) pass them in via keyword."""
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end_dt = (
        datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        if end_iso
        else None
    )
    if end_dt is None:
        from datetime import timedelta as _td

        end_dt = start_dt + _td(hours=5)
    total_tokens = (
        input_tokens + output_tokens + cache_create_tokens + cache_read_tokens
    )
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
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "cacheCreationInputTokens": cache_create_tokens,
                    "cacheReadInputTokens": cache_read_tokens,
                },
                "totalTokens": total_tokens,
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


# --- Pre-slice (plan-usage-updates branch): banner clock-time rendering -
#
# The Live view's window banner renders the active block's start and end
# times in the user's IANA zone via `utc_iso_to_local_clock`
# (live.py:172-177). The branch `plan-usage-updates` will rewrite
# `_render_window_banner` to plan-aware copy ("Quota window · X – Y ·
# resets in N min" on Pro/Max, "Current activity · X – Y" on Enterprise),
# touching the same code that calls those conversions.
#
# Existing banner coverage (test_ui_smoke.py:658-673) asserts the
# `tokenscope-live-banner` CSS class, the "Models in use" line, and
# the literal "Active block" substring — but does NOT assert that the
# converted clock-time substrings actually appear in the rendered
# banner. A rewrite that accidentally dropped `start_disp` or `end_disp`
# from the markdown template would produce a banner with the format
# label intact but the times missing, and no existing test would catch
# it.
#
# This pin locks the clock-time render path. Slice 1 must preserve it.


def test_live_banner_renders_start_and_end_times_in_local_zone(
    mock_ccusage, mock_ccusage_version, monkeypatch
) -> None:
    """The window banner renders both the active block's start and end
    clock times in the user's IANA zone, converted via
    `utc_iso_to_local_clock`.

    Fixture: block starts 13:00 UTC, ends 18:00 UTC. With
    `America/New_York` (EDT, UTC-4 in May), the rendered banner must
    contain `09:00` (start) AND `14:00` (end). A rewrite that drops
    either substring is caught here."""
    monkeypatch.setattr(
        "tokenscope.tz.detect_local_iana",
        lambda: "America/New_York",
    )
    _wire_live_fixtures(
        mock_ccusage,
        _make_active_block_payload(
            "2026-05-16T13:00:00.000Z",
            "2026-05-16T18:00:00.000Z",
        ),
    )

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    at.run()
    assert not at.exception, [str(e.value)[:200] for e in at.exception]

    # Locate the SPECIFIC markdown element that is the banner. The
    # `tokenscope-live-banner` substring also appears in the page's
    # injected CSS block as a rule selector (`.tokenscope-live-banner
    # { ... }`), so a naive concat-and-substring search would match
    # the stylesheet instead of the rendered banner div. The banner
    # element is uniquely identified by carrying "Models in use:"
    # (the second-line label) — that text appears nowhere else on
    # the page.
    banner_element = next(
        (m for m in at.markdown if "Models in use" in m.value),
        None,
    )
    assert banner_element is not None, (
        "banner element missing — the banner failed to render"
    )

    assert "09:00" in banner_element.value, (
        f"start-time `09:00` missing from banner element; value was: "
        f"{banner_element.value!r}"
    )
    assert "14:00" in banner_element.value, (
        f"end-time `14:00` missing from banner element; value was: "
        f"{banner_element.value!r}"
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


# --- BlockTokenCounts → kind mapping (pre-Slice B regression pins) -------
#
# The three consumers below all map `BlockTokenCounts` fields to the
# `input` / `output` / `cache_create` / `cache_read` kind keys:
#
#   * `live_token_kind_composition_bar` (charts.py)
#   * `_render_token_kind_kpis`         (live.py)
#   * `_render_token_kind_table`        (live.py)
#
# `analytics.block_token_counts_by_kind` is the canonical mapping;
# Slice B promoted it from `_block_token_counts_by_kind` and rewrote
# each consumer onto it. These tests pinned the per-consumer mapping
# BEFORE the slice landed — without them, an accidental field-name
# swap (e.g. `cache_create` reading `cache_read_input_tokens`) would
# still pass the existing label/colour assertions while displaying
# wildly wrong numbers in production.
#
# Distinct counts (11/22/33/44) make any swap unambiguous in the
# failure message.


def _block_with_distinct_kind_counts() -> BlockEntry:
    """Active block whose four kind counts are all distinct so an
    accidental swap between any two kinds shows up as a wrong number
    rather than a coincidentally-equal value."""
    return BlockEntry(
        id="2026-05-16T13:00:00.000Z",
        startTime="2026-05-16T13:00:00.000Z",
        endTime="2026-05-16T18:00:00.000Z",
        actualEndTime=None,
        isActive=True,
        isGap=False,
        entries=4,
        tokenCounts=BlockTokenCounts(
            inputTokens=11,
            outputTokens=22,
            cacheCreationInputTokens=33,
            cacheReadInputTokens=44,
        ),
        totalTokens=110,
        costUSD=1.0,
        models=["claude-opus-4-7"],
        burnRate=BurnRate(
            tokensPerMinute=1.0,
            tokensPerMinuteForIndicator=1.0,
            costPerHour=1.0,
        ),
        projection=Projection(
            totalTokens=200, totalCost=2.0, remainingMinutes=180,
        ),
    )


def test_live_token_kind_composition_bar_segment_widths_match_block_counts() -> None:
    """Each `go.Bar` trace in the composition bar must carry the
    token count for the kind it's named after — NOT some other
    kind's count.

    Pins the BlockTokenCounts→kind mapping in
    `charts.live_token_kind_composition_bar`. A regression that
    swapped, say, the `cache_create` trace's data source from
    `cache_creation_input_tokens` to `cache_read_input_tokens`
    would still pass the existing `test_token_kind_composition_bar_*`
    smoke tests (chart renders, four traces present) but display
    44 tokens under the amber Cache create label instead of 33.

    Distinct counts (11/22/33/44) make the failure message
    self-explanatory."""
    block = _block_with_distinct_kind_counts()
    fig = live_token_kind_composition_bar(block)
    assert fig is not None
    actual = {trace.name: int(trace.x[0]) for trace in fig.data}
    assert actual == {
        "input": 11,
        "output": 22,
        "cache_create": 33,
        "cache_read": 44,
    }, (
        f"composition bar segment-to-kind mapping wrong: {actual!r}"
    )


def test_live_token_kind_kpi_cards_pair_each_label_with_correct_count(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Each Live-view token-kind KPI card pairs its kind label with
    the count of THAT kind from `block.token_counts`. The card
    layout emits two markdown blocks per kind in column order:
    a header div carrying the label, then a value div carrying
    `format_compact_int(count) + " tokens"`. We walk the markdown
    stream and assert each header's immediately-following value div
    contains the right count.

    Pins the mapping in `live._render_token_kind_kpis`. A regression
    that swapped `cache_create` ↔ `cache_read` in the `counts` dict
    would still pass the existing label-presence and PALETTE-colour
    tests but display 44 tokens on the Cache create card."""
    payload = _make_active_block_payload(
        "2026-05-16T13:00:00.000Z",
        input_tokens=11,
        output_tokens=22,
        cache_create_tokens=33,
        cache_read_tokens=44,
    )
    _wire_live_fixtures(mock_ccusage, payload)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    at.run()

    assert not at.exception, [str(e.value)[:200] for e in at.exception]

    md_values = [m.value for m in at.markdown]
    expected = [
        ("Input", "11 tokens"),
        ("Output", "22 tokens"),
        ("Cache create", "33 tokens"),
        ("Cache read", "44 tokens"),
    ]
    for label, count_text in expected:
        header_indices = [
            i
            for i, m in enumerate(md_values)
            if "tokenscope-kind-card-label" in m and f">{label}<" in m
        ]
        assert len(header_indices) == 1, (
            f"expected exactly one KPI card header for {label!r}; "
            f"got {len(header_indices)}"
        )
        header_idx = header_indices[0]
        # The card's value div is the next markdown element on the page.
        assert header_idx + 1 < len(md_values), (
            f"no value div follows the {label!r} header"
        )
        value_md = md_values[header_idx + 1]
        assert "tokenscope-kind-card-value" in value_md, (
            f"markdown after {label!r} header isn't a value div: "
            f"{value_md!r}"
        )
        assert count_text in value_md, (
            f"{label!r} card carries wrong count; expected "
            f"{count_text!r}, value div was: {value_md!r}"
        )


def test_live_token_kind_table_rows_pair_each_kind_with_correct_count(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The token-kind mini-table beneath the composition bar must
    carry the right count and share% for each kind label.

    Pins the mapping in `live._render_token_kind_table`. The table
    is identified by its `Share %` column (unique to this surface;
    the Overview Cost composition uses `Share` without the percent
    sign, the Models breakdown uses `Share of cost`).

    Distinct counts (11/22/33/44) → distinct shares
    (10.0% / 20.0% / 30.0% / 40.0%), so a row-swap shows up as both
    the wrong count and the wrong share."""
    payload = _make_active_block_payload(
        "2026-05-16T13:00:00.000Z",
        input_tokens=11,
        output_tokens=22,
        cache_create_tokens=33,
        cache_read_tokens=44,
    )
    _wire_live_fixtures(mock_ccusage, payload)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    at.run()

    assert not at.exception, [str(e.value)[:200] for e in at.exception]

    kind_table = None
    for df_element in at.dataframe:
        df = df_element.value
        if "Kind" in df.columns and "Share %" in df.columns:
            kind_table = df
            break
    assert kind_table is not None, (
        "token-kind table not rendered on Live view; "
        f"dataframes seen: {[list(d.value.columns) for d in at.dataframe]!r}"
    )

    expected_rows = {
        "Input": ("11", "10.0%"),
        "Output": ("22", "20.0%"),
        "Cache create": ("33", "30.0%"),
        "Cache read": ("44", "40.0%"),
    }
    actual_by_kind = {
        row["Kind"]: (row["Tokens"], row["Share %"])
        for _, row in kind_table.iterrows()
    }
    assert actual_by_kind == expected_rows, (
        f"token-kind table row mapping wrong: {actual_by_kind!r}"
    )


# --- Pre-slice P1: compute-once invariant for Live block helpers -------
#
# Pinning the CURRENT call count of the two block-analytics helpers on
# the Live render path. Slice P1 will compute each helper exactly ONCE
# in `_live_panel` and pass the result down to all consumers; this test
# locks today's upper bounds so a regression that ADDS a redundant call
# fires immediately, even before Slice P1's per-slice tightening test.
#
# Today's actual counts (instrumented):
#
#   * `block_cost_by_kind`: 2 calls per render
#       - live.py:348 (`_render_token_kind_kpis`)
#       - live.py:455 (`_render_token_kind_table`)
#
#   * `block_token_counts_by_kind`: 5 calls per render
#       - live.py:354 (`_render_token_kind_kpis`, direct)
#       - live.py:461 (`_render_token_kind_table`, direct)
#       - charts.py:1402 (`live_token_kind_composition_bar` for the
#         segment widths)
#       - analytics.py:807 (`block_cost_by_kind` internal), called
#         twice via the two `block_cost_by_kind` invocations above
#
# Slice P1 will collapse these to ≤ 1 each (one call in `_live_panel`,
# results threaded through the renderer call chain). The slice's own
# tests will assert the tightened bound; this pin protects the looser
# upper bound against an UNRELATED regression.


def test_block_helpers_call_count_bounded_on_live_render(
    mock_ccusage, mock_ccusage_version, monkeypatch
) -> None:
    """Counter wrappers around `block_cost_by_kind` and
    `block_token_counts_by_kind` lock the upper bound of calls per
    Live render at today's measured values (2 / 5 respectively).
    Any regression that ADDS a redundant call fails here, before the
    Slice-P1 tightening test that asserts ≤ 1 each."""
    from tokenscope import analytics

    cost_calls: list[BlockEntry] = []
    counts_calls: list[BlockEntry] = []

    original_cost = analytics.block_cost_by_kind
    original_counts = analytics.block_token_counts_by_kind

    def _counted_cost(block: BlockEntry):
        cost_calls.append(block)
        return original_cost(block)

    def _counted_counts(block: BlockEntry) -> dict[str, int]:
        counts_calls.append(block)
        return original_counts(block)

    # Patch at every binding point. Both `live.py` and `charts.py`
    # import the names directly; `block_cost_by_kind` ALSO calls
    # `block_token_counts_by_kind` via the analytics module's own
    # binding. Patch all three locations so every call routes
    # through the counter.
    monkeypatch.setattr(analytics, "block_cost_by_kind", _counted_cost)
    monkeypatch.setattr(analytics, "block_token_counts_by_kind", _counted_counts)
    monkeypatch.setattr(
        "tokenscope.ui.live.block_cost_by_kind", _counted_cost
    )
    monkeypatch.setattr(
        "tokenscope.ui.live.block_token_counts_by_kind", _counted_counts
    )
    # `charts.live_token_kind_composition_bar` does a local import
    # of `block_token_counts_by_kind` inside the function body, so
    # the analytics-module patch above already catches it.

    payload = _make_active_block_payload(
        "2026-05-16T13:00:00.000Z",
        input_tokens=11, output_tokens=22,
        cache_create_tokens=33, cache_read_tokens=44,
    )
    _wire_live_fixtures(mock_ccusage, payload)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    at.run()

    assert not at.exception, [str(e.value)[:200] for e in at.exception]

    # Pre-slice-P1 upper bounds (measured on the current branch tip).
    # Slice P1 tightens both to ≤ 1 via its own per-slice tests.
    assert len(cost_calls) <= 2, (
        f"block_cost_by_kind called {len(cost_calls)} times per "
        f"Live render; pre-slice upper bound is 2"
    )
    assert len(counts_calls) <= 5, (
        f"block_token_counts_by_kind called {len(counts_calls)} times "
        f"per Live render; pre-slice upper bound is 5"
    )
    # Lower bound: each helper IS called at least once.
    assert len(cost_calls) >= 1
    assert len(counts_calls) >= 1
