"""Tests for tokenscope.ui.charts — Plotly figure builders.

These tests don't require a Streamlit runtime; the builders return
plotly.graph_objects.Figure objects we can inspect directly. The goal is
not to pin down Plotly internals — just to confirm that the data we
shaped flows through and that empty inputs short-circuit cleanly.
"""

from __future__ import annotations

import plotly.graph_objects as go
import pytest

from tokenscope.models import (
    BlockEntry,
    BlockTokenCounts,
    BurnRate,
    DailyEntry,
    DailyReport,
    ModelBreakdown,
    Projection,
    SessionEntry,
    Totals,
)
from tokenscope.ui.charts import (
    BRAND_HUE_SHADES,
    TOKEN_KIND_COLORS,
    _daily_metric_figure,
    apply_enterprise_style,
    burn_gauge,
    cache_hit_ratio_line,
    cost_trend_with_rolling,
    donut_cost_by_model,
    family_color_map,
    session_blocks_timeline,
    session_token_mix,
    single_family_token_bar,
    token_flow_sankey,
    token_mix_non_cache_percent_bar,
    token_mix_percent_bar,
)


def _entry(date_str: str, *, cost: float, model: str) -> DailyEntry:
    return DailyEntry(
        date=date_str,
        inputTokens=100,
        outputTokens=200,
        cacheCreationTokens=300,
        cacheReadTokens=400,
        totalTokens=1000,
        totalCost=cost,
        modelsUsed=[model],
        modelBreakdowns=[
            ModelBreakdown(
                modelName=model,
                inputTokens=100,
                outputTokens=200,
                cacheCreationTokens=300,
                cacheReadTokens=400,
                cost=cost,
            )
        ],
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


# ---------- _daily_metric_figure (the single-day fallback helper) ----------


def _two_day_df():
    import pandas as pd
    return pd.DataFrame(
        [
            {"date": "2026-05-15", "y": 1.0, "g": "a"},
            {"date": "2026-05-15", "y": 2.0, "g": "b"},
            {"date": "2026-05-16", "y": 3.0, "g": "a"},
            {"date": "2026-05-16", "y": 4.0, "g": "b"},
        ]
    )


def _one_day_df():
    import pandas as pd
    return pd.DataFrame(
        [
            {"date": "2026-05-16", "y": 5.0, "g": "a"},
            {"date": "2026-05-16", "y": 6.0, "g": "b"},
        ]
    )


def test_daily_metric_figure_multi_day_area_uses_stackgroup() -> None:
    fig = _daily_metric_figure(
        _two_day_df(),
        x="date", y="y", color="g",
        labels={"date": "Date", "y": "Y", "g": "G"},
        multi_day="area",
    )
    # px.area renders scatter traces with stackgroup set (that's how
    # plotly_express distinguishes "area" from "line" — fill is None,
    # the stackgroup attr carries the layering identity).
    assert all(t.type == "scatter" for t in fig.data)
    assert all(t.stackgroup for t in fig.data), (
        f"expected stackgroup on every trace, got "
        f"{[t.stackgroup for t in fig.data]}"
    )


def test_daily_metric_figure_multi_day_line_has_markers_and_lines() -> None:
    fig = _daily_metric_figure(
        _two_day_df()[["date", "y"]].drop_duplicates(subset=["date"]),
        x="date", y="y",
        labels={"date": "Date", "y": "Y"},
        multi_day="line",
    )
    assert len(fig.data) == 1
    assert fig.data[0].type == "scatter"
    assert fig.data[0].mode == "lines+markers"


def test_daily_metric_figure_single_day_forces_bar_with_stack_when_coloured() -> None:
    fig = _daily_metric_figure(
        _one_day_df(),
        x="date", y="y", color="g",
        labels={"date": "Date", "y": "Y", "g": "G"},
        multi_day="area",
    )
    assert all(t.type == "bar" for t in fig.data)
    assert fig.layout.barmode == "stack"


def test_daily_metric_figure_single_day_uncoloured_bar_no_stack_directive() -> None:
    """When ``color`` is None there's no series split, so barmode must not
    be touched — leaving Plotly's default is the correct authoritative
    behaviour (no spurious layout override)."""
    fig = _daily_metric_figure(
        _one_day_df()[["date", "y"]].drop_duplicates(subset=["date"]),
        x="date", y="y",
        labels={"date": "Date", "y": "Y"},
        multi_day="line",
    )
    assert len(fig.data) == 1
    assert fig.data[0].type == "bar"
    # We did not set barmode in this branch — Plotly default ('group') stands.
    assert fig.layout.barmode != "stack"


# ---------- cost_trend_with_rolling ----------


def test_cost_trend_returns_figure_with_family_traces() -> None:
    report = _report(
        [
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=1.0, model="claude-haiku-4-5-20251001"),
            _entry("2026-05-16", cost=4.0, model="claude-opus-4-7"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    assert isinstance(fig, go.Figure)
    # Both family bands + the rolling-average overlay are present.
    trace_names = {t.name for t in fig.data}
    assert "opus" in trace_names and "haiku" in trace_names
    assert "7-day avg" in trace_names


def test_cost_trend_empty_returns_none() -> None:
    assert cost_trend_with_rolling(_report([])) is None


def test_cost_trend_single_day_uses_bar_not_area() -> None:
    """Single-day windows fall back to a stacked bar — px.area paints
    zero width with one x-value and the chart looks empty."""
    report = _report(
        [
            _entry("2026-05-16", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=1.0, model="claude-haiku-4-5-20251001"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    assert isinstance(fig, go.Figure)
    assert all(t.type == "bar" for t in fig.data), (
        f"expected bar traces on single-day window; got {[t.type for t in fig.data]}"
    )
    assert fig.layout.barmode == "stack"


def test_cost_trend_uses_categorical_palette_via_color_map() -> None:
    """Family bands draw from the categorical palette via an explicit
    `family_color_map` so each family gets a distinct hue rather than
    a tonal shade of one color. The mapping is positional on sorted
    family names, so the test computes the expected color the same
    way the chart does."""
    report = _report(
        [
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=1.0, model="claude-haiku-4-5-20251001"),
            _entry("2026-05-16", cost=4.0, model="claude-opus-4-7"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    expected_colors = family_color_map(["opus", "haiku"])
    for family in ("opus", "haiku"):
        band = next(t for t in fig.data if t.name == family)
        line_color = band.line.color if hasattr(band, "line") and band.line else None
        fill_color = band.fillcolor if hasattr(band, "fillcolor") else None
        assert line_color == expected_colors[family] or fill_color == expected_colors[family], (
            f"family {family!r} expected color {expected_colors[family]!r}; "
            f"got line={line_color!r} fill={fill_color!r}"
        )


def _trace_color(trace) -> str | None:
    """Best-effort extraction of a trace's visual color across the
    Plotly trace types we use (Scatter for area, Bar for fallback,
    Scatter for line overlay). The exact attribute that carries the
    rendered color varies — we check the common locations."""
    if hasattr(trace, "line") and trace.line and trace.line.color:
        return trace.line.color
    if hasattr(trace, "marker") and trace.marker and trace.marker.color:
        return trace.marker.color
    return getattr(trace, "fillcolor", None)


def test_cost_trend_distinct_family_colors_not_shades_of_one() -> None:
    """Regression for the grayscale-palette bug: two families with
    different names must get visually distinct hues, not adjacent
    shades of the same color. Uses a multi-day fixture so the chart
    builds as an area (where line.color carries the family hue),
    not the single-day bar fallback."""
    report = _report(
        [
            _entry("2026-05-14", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-14", cost=1.0, model="claude-haiku-4-5-20251001"),
            _entry("2026-05-15", cost=4.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=2.0, model="claude-haiku-4-5-20251001"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    family_colors: dict[str, str | None] = {}
    for trace in fig.data:
        if trace.name in {"opus", "haiku"}:
            family_colors[trace.name] = _trace_color(trace)
    assert None not in family_colors.values(), (
        f"opus and haiku must have explicit colors; got {family_colors!r}"
    )
    assert len(set(family_colors.values())) == 2, (
        f"opus and haiku must use distinct hues; got {family_colors!r}"
    )


def test_cost_trend_adds_spike_annotation_when_provided() -> None:
    """`spike=(date, cost)` adds a Plotly annotation calling out the
    outlier day. Without the kwarg, no annotation is drawn."""
    report = _report(
        [
            _entry("2026-04-18", cost=400.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=4.0, model="claude-opus-4-7"),
        ]
    )
    annotated = cost_trend_with_rolling(report, spike=("2026-04-18", 400.0))
    plain = cost_trend_with_rolling(report)
    assert annotated is not None and plain is not None
    annotated_texts = [a.text for a in annotated.layout.annotations or []]
    plain_texts = [a.text for a in plain.layout.annotations or []]
    assert any(
        "2026-04-18" in t and "400" in t for t in annotated_texts
    ), f"expected spike annotation; got: {annotated_texts!r}"
    assert plain_texts == [], (
        f"expected no annotations without spike kwarg; got: {plain_texts!r}"
    )


# ---------- token_mix_percent_bar ----------


def test_token_mix_percent_returns_figure_with_four_kinds() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_percent_bar(report)
    assert isinstance(fig, go.Figure)
    kinds = {t.name for t in fig.data}
    assert kinds == {"input", "output", "cache_create", "cache_read"}


def test_token_mix_percent_each_day_sums_to_one_hundred() -> None:
    """The chart answers a composition question — each day's bars
    must sum to 100% so the user reads the mix directly."""
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_percent_bar(report)
    # Sum the y-values across all traces for the single day.
    total = sum(float(t.y[0]) for t in fig.data)
    assert total == pytest.approx(100.0, abs=0.01)


def test_token_mix_percent_uses_stack_not_overlay() -> None:
    """Percent-stacked = barmode='stack'. Overlay would have all four
    kinds painting from 0 to their own percent, defeating the mix
    visualisation."""
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_percent_bar(report)
    assert fig.layout.barmode == "stack"
    # Y-axis range is locked to [0, 100] so the percent-stacks read
    # consistently across days.
    assert fig.layout.yaxis.range is not None
    assert list(fig.layout.yaxis.range) == [0, 100]


def test_token_mix_percent_empty_returns_none() -> None:
    assert token_mix_percent_bar(_report([])) is None


# ---------- regression: no `undefined` trace from edge-case data ---------
#
# The Daily-cost and Token-mix charts shipped to the user with a literal
# `undefined` legend entry. Root cause: when the data contained a
# category not anticipated by the classifier (e.g. a new model id whose
# family suffix the classifier returned empty for, or a stray token-kind
# row), `color_discrete_sequence=` would emit a Plotly trace whose name
# stringified to `undefined` in the JS legend.
#
# These tests assert the post-fix contract: regardless of what the data
# layer throws at the chart builders, the resulting figures contain only
# traces whose names are in the documented allowed set. They run against
# pathological fixtures (unknown model, blank model, empty modelsUsed,
# entirely-foreign model) so a future regression in the classifier or
# data emitter can't silently slip the bug back in.


def _entry_with_models(
    date_str: str, models: list[str], cost_per_model: float = 1.0
) -> DailyEntry:
    """Build a DailyEntry whose model_breakdowns reflect `models`
    one-to-one. Used to construct pathological-input scenarios for
    the regression tests."""
    breakdowns = [
        ModelBreakdown(
            modelName=m,
            inputTokens=10,
            outputTokens=20,
            cacheCreationTokens=30,
            cacheReadTokens=40,
            cost=cost_per_model,
        )
        for m in models
    ]
    return DailyEntry(
        date=date_str,
        inputTokens=10 * max(len(models), 1),
        outputTokens=20 * max(len(models), 1),
        cacheCreationTokens=30 * max(len(models), 1),
        cacheReadTokens=40 * max(len(models), 1),
        totalTokens=100 * max(len(models), 1),
        totalCost=cost_per_model * max(len(models), 1),
        modelsUsed=models,
        modelBreakdowns=breakdowns,
    )


_PATHOLOGICAL_DAILY_FIXTURES = {
    "clean_two_families": [
        _entry_with_models("2026-05-15", ["claude-opus-4-7"], 5.0),
        _entry_with_models("2026-05-15", ["claude-haiku-4-5-20251001"], 1.0),
        _entry_with_models("2026-05-16", ["claude-opus-4-7"], 3.0),
    ],
    "deprecated_family_alongside_current": [
        _entry_with_models("2026-05-15", ["claude-opus-4-7"], 5.0),
        _entry_with_models("2026-05-15", ["claude-opus-4-6"], 4.0),
        _entry_with_models("2026-05-16", ["claude-haiku-4-5-20251001"], 1.0),
    ],
    "third_family_beyond_active_filter": [
        _entry_with_models("2026-05-15", ["claude-opus-4-7"], 5.0),
        _entry_with_models("2026-05-15", ["claude-haiku-4-5-20251001"], 1.0),
        _entry_with_models("2026-05-16", ["claude-sonnet-4-5-20251022"], 2.0),
    ],
    "unknown_future_model_id": [
        _entry_with_models("2026-05-15", ["claude-opus-4-7"], 5.0),
        _entry_with_models(
            "2026-05-16",
            ["some-future-model-anthropic-hasnt-shipped-yet"],
            2.0,
        ),
    ],
    "blank_model_id_slipped_through": [
        _entry_with_models("2026-05-15", ["claude-opus-4-7"], 5.0),
        _entry_with_models("2026-05-16", [""], 1.0),
    ],
}


@pytest.mark.parametrize("fixture_name", list(_PATHOLOGICAL_DAILY_FIXTURES))
def test_cost_trend_chart_has_no_undefined_trace(fixture_name: str) -> None:
    """Regression for the `undefined` legend entry on Daily cost.

    Across every pathological input (deprecated family, third family,
    totally-unknown model id, blank id), the chart must produce traces
    whose names are real human-readable strings — never `"undefined"`,
    never `None`, never `""`."""
    entries = _PATHOLOGICAL_DAILY_FIXTURES[fixture_name]
    fig = cost_trend_with_rolling(_report(entries))
    assert fig is not None, f"chart must render for fixture {fixture_name!r}"
    names = [t.name for t in fig.data]
    for name in names:
        assert name is not None, (
            f"[{fixture_name}] trace with None name: {names!r}"
        )
        assert name != "", (
            f"[{fixture_name}] trace with empty-string name: {names!r}"
        )
        assert name.lower() != "undefined", (
            f"[{fixture_name}] `undefined` trace leaked: {names!r}"
        )
        assert name.lower() != "nan", (
            f"[{fixture_name}] `nan` trace leaked: {names!r}"
        )


def _figure_contains_undefined(fig) -> bool:
    """Walk the full figure JSON and return True if the literal string
    `undefined` appears anywhere — legend, axis labels, hover, names,
    annotations, anything. Plotly's JS renderer surfaces this string
    when any field-level value stringifies that way."""
    import json

    serialized = json.dumps(fig.to_dict(), default=str).lower()
    return "undefined" in serialized


@pytest.mark.parametrize("fixture_name", list(_PATHOLOGICAL_DAILY_FIXTURES))
def test_no_undefined_anywhere_in_cost_trend_figure_json(
    fixture_name: str,
) -> None:
    """End-to-end JSON-walk: serialize the entire figure and scan the
    whole structure for the string `undefined`. If the chart is built
    correctly, this string never appears in legend / axes / hover /
    annotations regardless of what pathological input the data layer
    produces."""
    entries = _PATHOLOGICAL_DAILY_FIXTURES[fixture_name]
    fig = cost_trend_with_rolling(_report(entries))
    assert fig is not None
    assert not _figure_contains_undefined(fig), (
        f"[{fixture_name}] cost_trend figure contains `undefined` "
        f"somewhere in its serialized form"
    )


@pytest.mark.parametrize("fixture_name", list(_PATHOLOGICAL_DAILY_FIXTURES))
def test_no_undefined_anywhere_in_token_mix_figure_json(
    fixture_name: str,
) -> None:
    """Same JSON-walk contract for the Token-mix chart."""
    entries = _PATHOLOGICAL_DAILY_FIXTURES[fixture_name]
    fig = token_mix_percent_bar(_report(entries))
    assert fig is not None
    assert not _figure_contains_undefined(fig), (
        f"[{fixture_name}] token_mix figure contains `undefined` "
        f"somewhere in its serialized form"
    )


@pytest.mark.parametrize("fixture_name", list(_PATHOLOGICAL_DAILY_FIXTURES))
def test_no_undefined_anywhere_in_cost_trend_overlay_mode(
    fixture_name: str,
) -> None:
    """Overlay mode (the user's small-family-visible variant) carries
    the same `undefined`-free contract."""
    entries = _PATHOLOGICAL_DAILY_FIXTURES[fixture_name]
    fig = cost_trend_with_rolling(_report(entries), mode="overlay")
    assert fig is not None
    assert not _figure_contains_undefined(fig)


@pytest.mark.parametrize("fixture_name", list(_PATHOLOGICAL_DAILY_FIXTURES))
def test_token_mix_chart_has_exactly_four_documented_kinds(
    fixture_name: str,
) -> None:
    """Regression for the `undefined` legend entry on Token mix.

    Token kinds are a fixed enum at the data layer (`daily_token_mix`
    emits only the four). The chart's defensive `isin(TOKEN_KIND_LABELS)`
    filter is belt-and-braces — even if a future emitter drift added a
    5th kind row, the chart wouldn't paint it. Assert here that the
    chart's trace set is exactly the documented four regardless of
    what model-id pathology the input has."""
    entries = _PATHOLOGICAL_DAILY_FIXTURES[fixture_name]
    fig = token_mix_percent_bar(_report(entries))
    assert fig is not None, f"chart must render for fixture {fixture_name!r}"
    names = {t.name for t in fig.data}
    assert names == {"input", "output", "cache_create", "cache_read"}, (
        f"[{fixture_name}] expected exactly the four kinds; got {names!r}"
    )


def test_scrub_undefined_traces_strips_falsy_and_undefined_names(caplog) -> None:
    """The final scrubber in `apply_enterprise_style` drops any trace
    whose name would render as `undefined` in the Plotly JS legend.
    Defensive: chart builders are supposed to make this impossible,
    but a future regression in any builder is caught here."""
    import logging
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[1, 2], y=[1, 2], name="real"))
    fig.add_trace(go.Scatter(x=[1, 2], y=[3, 4], name=""))
    fig.add_trace(go.Scatter(x=[1, 2], y=[5, 6], name="undefined"))
    fig.add_trace(go.Scatter(x=[1, 2], y=[7, 8], name=None))
    fig.add_trace(go.Scatter(x=[1, 2], y=[9, 10], name="nan"))

    caplog.set_level(logging.WARNING, logger="tokenscope.ui.charts")
    styled = apply_enterprise_style(fig)
    names = {t.name for t in styled.data}
    assert names == {"real"}, (
        f"only `real` should survive; got {names!r}"
    )
    # Production-side data is supposed to make this impossible; the
    # warning log gives operators a signal that something upstream
    # produced phantom names.
    assert any(
        "chart.phantom_trace_scrubbed" in r.message
        for r in caplog.records
    ), f"expected scrub warning; got records: {[r.message for r in caplog.records]}"


def test_scrub_undefined_traces_is_idempotent_when_all_clean() -> None:
    """No-op when every trace already has a real name. No log fires,
    no traces dropped."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[1], y=[1], name="a"))
    fig.add_trace(go.Scatter(x=[1], y=[2], name="b"))
    styled = apply_enterprise_style(fig)
    assert [t.name for t in styled.data] == ["a", "b"]


def test_cost_trend_overlay_mode_keeps_small_family_visible() -> None:
    """Overlay mode: each family gets its own non-stacked area so a
    dominant family can't crush the smaller ones against the
    baseline. Small-family band is drawn LAST so it sits on top
    visually."""
    report = _report(
        [
            _entry("2026-05-14", cost=400.0, model="claude-opus-4-7"),
            _entry("2026-05-14", cost=1.0, model="claude-haiku-4-5-20251001"),
            _entry("2026-05-15", cost=380.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=2.0, model="claude-haiku-4-5-20251001"),
        ]
    )
    fig = cost_trend_with_rolling(report, mode="overlay")
    family_traces = [t for t in fig.data if t.name in {"opus", "haiku"}]
    # Both families present, drawn smallest-last (haiku traces AFTER opus).
    assert [t.name for t in family_traces] == ["opus", "haiku"]
    # Bands are non-stacked: each has its own y trajectory matching
    # the per-family raw cost (not cumulative).
    haiku = next(t for t in family_traces if t.name == "haiku")
    haiku_ys = list(haiku.y)
    # haiku's costs were 1 and 2 — not opus+haiku=401 / 382.
    assert max(haiku_ys) <= 2.5, (
        f"haiku band carries non-stacked absolute values; got {haiku_ys!r}"
    )


def test_with_alpha_helper_passes_through_invalid_hex() -> None:
    """`_with_alpha` accepts `#RRGGBB`. Defensive: shorter / longer
    inputs (in case a future palette has 3-digit hex or named colors)
    pass through unchanged rather than crashing."""
    from tokenscope.ui.charts import _with_alpha

    assert _with_alpha("#FF0000", 0.5) == "rgba(255,0,0,0.500)"
    assert _with_alpha("not-a-hex", 0.5) == "not-a-hex"
    assert _with_alpha("#FFF", 0.5) == "#FFF"


def test_cost_trend_rejects_unknown_mode() -> None:
    """Defensive: a typo'd mode argument fails loudly rather than
    silently picking one branch."""
    report = _report([_entry("2026-05-15", cost=5.0, model="claude-opus-4-7")])
    with pytest.raises(ValueError, match="mode must be"):
        cost_trend_with_rolling(report, mode="bogus")


def test_token_mix_returns_none_when_all_kinds_unknown() -> None:
    """Defensive edge case: if a future schema drift made *every* row
    carry an unknown kind, the filter would empty the df entirely.
    Returning None lets the caller render an empty state instead of
    a blank Plotly canvas."""
    import tokenscope.ui.charts as charts_module

    real_daily_token_mix = charts_module.daily_token_mix
    charts_module.daily_token_mix = lambda _report: [
        {"date": "2026-05-16", "kind": "experimental", "tokens": 100},
        {"date": "2026-05-16", "kind": "phantom", "tokens": 200},
    ]
    try:
        report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
        fig = token_mix_percent_bar(report)
    finally:
        charts_module.daily_token_mix = real_daily_token_mix
    assert fig is None


def test_token_mix_chart_filters_unknown_kinds_defensively() -> None:
    """If a future schema drift introduced a 5th kind row, the chart's
    `isin(TOKEN_KIND_LABELS)` filter would drop it before reaching
    Plotly. Verified by mocking `daily_token_mix` to emit a phantom
    `"experimental"` kind alongside the four real ones."""
    import tokenscope.ui.charts as charts_module

    real_daily_token_mix = charts_module.daily_token_mix

    def spiked(daily_report):
        rows = real_daily_token_mix(daily_report)
        if rows:
            rows.append(
                {"date": rows[0]["date"], "kind": "experimental", "tokens": 999}
            )
        return rows

    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    charts_module.daily_token_mix = spiked
    try:
        fig = token_mix_percent_bar(report)
    finally:
        charts_module.daily_token_mix = real_daily_token_mix
    names = {t.name for t in fig.data}
    assert "experimental" not in names, (
        f"defensive filter failed — `experimental` reached Plotly: {names!r}"
    )
    assert names == {"input", "output", "cache_create", "cache_read"}


# ---------- categorical palette ----------


def test_token_kind_colors_are_distinct_hues() -> None:
    """Categorical data needs distinguishable hues, not shades of one.
    The four kinds must map to four different colors — adjacent grays
    were the previous palette bug."""
    colors = set(TOKEN_KIND_COLORS.values())
    assert len(colors) == 4, f"TOKEN_KIND_COLORS must be 4 distinct; got {TOKEN_KIND_COLORS!r}"


def test_family_color_map_assigns_distinct_colors_to_distinct_families() -> None:
    mapping = family_color_map(["opus", "haiku", "sonnet"])
    assert len(set(mapping.values())) == 3
    assert all(c.startswith("#") for c in mapping.values())


def test_family_color_map_stable_under_input_order() -> None:
    """Sorting the input means `family_color_map(["opus", "haiku"])`
    and `family_color_map(["haiku", "opus"])` agree on which hue each
    family gets — successive renders never reshuffle colors."""
    assert family_color_map(["opus", "haiku"]) == family_color_map(
        ["haiku", "opus"]
    )


def test_family_color_map_falls_back_to_neutral_for_blank() -> None:
    """A blank family name (defensive case) gets a neutral gray —
    never an empty-string color that browsers would render as
    transparent / default."""
    mapping = family_color_map([""])
    assert mapping[""].startswith("#")


def test_family_color_map_known_families_get_canonical_colors() -> None:
    """`opus` / `sonnet` / `haiku` always get the same hue regardless
    of which other families are in the window. Users build muscle
    memory — "opus is the indigo band" — and that breaks if the same
    family gets a different color across renders."""
    full = family_color_map(["opus", "sonnet", "haiku"])
    partial_oh = family_color_map(["opus", "haiku"])
    partial_os = family_color_map(["opus", "sonnet"])
    just_haiku = family_color_map(["haiku"])

    assert full["opus"] == partial_oh["opus"] == partial_os["opus"]
    assert full["haiku"] == partial_oh["haiku"] == just_haiku["haiku"]
    assert full["sonnet"] == partial_os["sonnet"]
    # And the three canonical colors are distinct.
    assert len({full["opus"], full["sonnet"], full["haiku"]}) == 3


def test_family_color_map_unknown_family_gets_palette_fallback() -> None:
    """A future Anthropic family Anthropic hasn't shipped yet — or a
    non-Claude model id like `gpt-4o` — gets a palette color that's
    distinct from the three branded slots."""
    mapping = family_color_map(["opus", "future-family-x"])
    assert mapping["opus"] != mapping["future-family-x"]
    assert mapping["future-family-x"].startswith("#")


def test_token_kind_and_family_palettes_share_no_color() -> None:
    """Different concept-groups in the dashboard (token kinds vs
    model families) must NOT share colors — if `input` and `opus`
    both painted indigo, users would conflate the two across the
    Token-mix and Daily-cost charts."""
    family_palette = family_color_map(["opus", "sonnet", "haiku"])
    family_colors = set(family_palette.values())
    kind_colors = set(TOKEN_KIND_COLORS.values())
    overlap = family_colors & kind_colors
    assert not overlap, (
        f"token-kind and family palettes share color(s): {overlap!r}"
    )


def test_family_color_map_unknown_sentinel_gets_neutral() -> None:
    """The `UNKNOWN_MODEL_FAMILY` ("other") sentinel renders neutral
    gray so the band visually reads as "uncategorised", not as a
    peer of the branded Anthropic families."""
    from tokenscope.analytics import UNKNOWN_MODEL_FAMILY

    mapping = family_color_map(["opus", UNKNOWN_MODEL_FAMILY])
    assert mapping[UNKNOWN_MODEL_FAMILY] != mapping["opus"]
    # The same neutral color is used as for blank families.
    assert mapping[UNKNOWN_MODEL_FAMILY] == family_color_map([""])[""]


def test_token_mix_non_cache_renders_three_kinds_only() -> None:
    """Non-cache mini chart excludes cache_read so the variance the
    main token-mix crushes becomes legible. Exactly three traces,
    each rebased to the non-cache subtotal."""
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_non_cache_percent_bar(report)
    assert isinstance(fig, go.Figure)
    names = {t.name for t in fig.data}
    assert names == {"input", "output", "cache_create"}
    # Each day's three bars sum to 100% (non-cache total = 100%).
    day_total = sum(float(t.y[0]) for t in fig.data)
    assert day_total == pytest.approx(100.0, abs=0.01)


def test_token_mix_non_cache_uses_stack_mode() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_mix_non_cache_percent_bar(report)
    assert fig.layout.barmode == "stack"
    assert list(fig.layout.yaxis.range) == [0, 100]


def test_token_mix_non_cache_empty_returns_none() -> None:
    assert token_mix_non_cache_percent_bar(_report([])) is None


def test_token_mix_non_cache_returns_none_when_no_non_cache_tokens() -> None:
    """If every entry has zero input/output/cache_create tokens, the
    chart would render all-zero bars — the empty state is better
    surfaced as a caption by the caller. Return None."""
    cache_only_entry = DailyEntry(
        date="2026-05-16",
        inputTokens=0,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=1_000_000,
        totalTokens=1_000_000,
        totalCost=1.0,
        modelsUsed=["claude-opus-4-7"],
        modelBreakdowns=[
            ModelBreakdown(
                modelName="claude-opus-4-7",
                inputTokens=0,
                outputTokens=0,
                cacheCreationTokens=0,
                cacheReadTokens=1_000_000,
                cost=1.0,
            )
        ],
    )
    report = DailyReport(
        daily=[cache_only_entry],
        totals=Totals(
            inputTokens=0,
            outputTokens=0,
            cacheCreationTokens=0,
            cacheReadTokens=1_000_000,
            totalTokens=1_000_000,
            totalCost=1.0,
        ),
    )
    assert token_mix_non_cache_percent_bar(report) is None


@pytest.mark.parametrize("fixture_name", list(_PATHOLOGICAL_DAILY_FIXTURES))
def test_token_mix_non_cache_chart_has_no_undefined_trace(
    fixture_name: str,
) -> None:
    """Same `undefined`-regression contract as the main chart: across
    every pathological input, the non-cache chart's traces must be
    exactly the documented three kinds."""
    entries = _PATHOLOGICAL_DAILY_FIXTURES[fixture_name]
    fig = token_mix_non_cache_percent_bar(_report(entries))
    if fig is None:
        return  # fixture's tokens were all-cache_read — acceptable
    names = {t.name for t in fig.data}
    assert names == {"input", "output", "cache_create"}, (
        f"[{fixture_name}] non-cache chart got unexpected traces: {names!r}"
    )


def test_cost_trend_with_every_known_anthropic_family_renders_distinctly() -> None:
    """A future user runs with all three currently-known Anthropic
    families (opus + sonnet + haiku) in the same window. Every family
    must get a distinct, real color — no `undefined` even with three
    bands stacked."""
    report = _report(
        [
            _entry("2026-05-14", cost=5.0, model="claude-opus-4-7"),
            _entry("2026-05-14", cost=3.0, model="claude-sonnet-4-6"),
            _entry("2026-05-14", cost=1.0, model="claude-haiku-4-5-20251001"),
            _entry("2026-05-15", cost=4.0, model="claude-opus-4-7"),
            _entry("2026-05-15", cost=2.0, model="claude-sonnet-4-6"),
            _entry("2026-05-15", cost=0.5, model="claude-haiku-4-5-20251001"),
        ]
    )
    fig = cost_trend_with_rolling(report)
    family_traces = {t.name: t for t in fig.data if t.name in {"opus", "sonnet", "haiku"}}
    assert set(family_traces.keys()) == {"opus", "sonnet", "haiku"}
    colors = {name: _trace_color(t) for name, t in family_traces.items()}
    assert None not in colors.values()
    assert len(set(colors.values())) == 3, f"three families need three hues; got {colors!r}"


def test_token_mix_percent_zero_day_has_zero_percents() -> None:
    """A zero-token day mustn't trigger a divide-by-zero — its
    percents are all 0 (no bar visible at that x)."""
    zero_entry = DailyEntry(
        date="2026-05-16",
        inputTokens=0,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=0,
        totalTokens=0,
        totalCost=0.0,
        modelsUsed=[],
        modelBreakdowns=[],
    )
    report = DailyReport(
        daily=[zero_entry],
        totals=Totals(
            inputTokens=0,
            outputTokens=0,
            cacheCreationTokens=0,
            cacheReadTokens=0,
            totalTokens=0,
            totalCost=0.0,
        ),
    )
    fig = token_mix_percent_bar(report)
    # Figure should still build (no NaN crash), just with zero-height bars.
    assert isinstance(fig, go.Figure)
    for trace in fig.data:
        assert all(y == 0 for y in trace.y)


# ---------- apply_enterprise_style ----------


def test_apply_enterprise_style_clears_title_and_axis_titles() -> None:
    """The enterprise style drops any in-chart title and axis titles —
    the section H3 above the chart owns the title, the ticks own the
    axes."""
    fig = go.Figure()
    fig.update_layout(
        title="Should be removed",
        xaxis_title="Date",
        yaxis_title="Cost (USD)",
    )
    styled = apply_enterprise_style(fig)
    assert styled.layout.title.text is None
    assert styled.layout.xaxis.title.text is None
    assert styled.layout.yaxis.title.text is None


def test_apply_enterprise_style_horizontal_gridlines_only() -> None:
    """Horizontal dotted gridlines only — no vertical, no axis spines."""
    fig = apply_enterprise_style(go.Figure())
    assert fig.layout.xaxis.showgrid is False
    assert fig.layout.yaxis.showgrid is True
    assert fig.layout.xaxis.zeroline is False
    assert fig.layout.yaxis.zeroline is False
    assert fig.layout.xaxis.showline is False
    assert fig.layout.yaxis.showline is False


def test_apply_enterprise_style_is_idempotent() -> None:
    """Applying the style twice produces the same layout as applying
    it once. Builders compose freely."""
    fig = go.Figure()
    once = apply_enterprise_style(fig).to_dict()["layout"]
    twice = apply_enterprise_style(apply_enterprise_style(go.Figure())).to_dict()["layout"]
    assert once == twice


# ---------- donut_cost_by_model ----------


def test_donut_cost_by_model_returns_figure() -> None:
    entry = _entry("2026-05-16", cost=10.0, model="claude-opus-4-7")
    fig = donut_cost_by_model(entry)
    assert isinstance(fig, go.Figure)
    # One pie trace, with one slice per model.
    assert len(fig.data) == 1
    assert list(fig.data[0].labels) == ["claude-opus-4-7"]
    assert list(fig.data[0].values) == [10.0]


def test_donut_cost_by_model_no_breakdowns_returns_none() -> None:
    entry = DailyEntry(
        date="2026-05-16",
        inputTokens=0,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=0,
        totalTokens=0,
        totalCost=0.0,
        modelsUsed=[],
        modelBreakdowns=[],
    )
    assert donut_cost_by_model(entry) is None


# ---------- session_token_mix ----------


def _session(*, cost: float = 1.0) -> SessionEntry:
    return SessionEntry(
        sessionId="sess-1",
        inputTokens=10,
        outputTokens=20,
        cacheCreationTokens=30,
        cacheReadTokens=40,
        totalTokens=100,
        totalCost=cost,
        modelsUsed=["claude-opus-4-7"],
        modelBreakdowns=[
            ModelBreakdown(
                modelName="claude-opus-4-7",
                inputTokens=10,
                outputTokens=20,
                cacheCreationTokens=30,
                cacheReadTokens=40,
                cost=cost,
            )
        ],
        lastActivity="2026-05-16",
        projectPath="-Users-q",
    )


def test_session_token_mix_has_four_bars() -> None:
    fig = session_token_mix(_session())
    assert isinstance(fig, go.Figure)
    kinds = {t.name for t in fig.data}
    assert kinds == {"input", "output", "cache_create", "cache_read"}


# ---------- burn_gauge ----------


def _block_with_burn() -> BlockEntry:
    return BlockEntry(
        id="2026-05-16T13:00:00.000Z",
        startTime="2026-05-16T13:00:00.000Z",
        endTime="2026-05-16T18:00:00.000Z",
        actualEndTime=None,
        isActive=True,
        isGap=False,
        entries=1,
        tokenCounts=BlockTokenCounts(
            inputTokens=10, outputTokens=20, cacheCreationInputTokens=30, cacheReadInputTokens=40
        ),
        totalTokens=100,
        costUSD=1.0,
        models=["claude-opus-4-7"],
        burnRate=BurnRate(
            tokensPerMinute=1.0,
            tokensPerMinuteForIndicator=1.0,
            costPerHour=8.83,
        ),
        projection=Projection(totalTokens=999, totalCost=38.48, remainingMinutes=259),
    )


def test_burn_gauge_returns_indicator_figure() -> None:
    fig = burn_gauge(_block_with_burn())
    assert isinstance(fig, go.Figure)
    # Single Indicator trace.
    assert len(fig.data) == 1
    assert fig.data[0].value == 8.83


def test_burn_gauge_with_typical_renders_threshold_marker() -> None:
    """When ``typical`` is provided and > 0, the gauge gets a red
    threshold line at that value — visual cue for "above/below my usual
    burn". Threshold structure: gauge.threshold = {"line": {"color":
    "#d62728", ...}, "thickness": 0.85, "value": typical}."""
    fig = burn_gauge(_block_with_burn(), typical=5.0)
    assert isinstance(fig, go.Figure)
    threshold = fig.data[0].gauge.threshold
    assert threshold is not None
    assert threshold.value == 5.0
    assert threshold.line.color == "#d62728"


def test_burn_gauge_typical_zero_does_not_set_threshold() -> None:
    """A typical of 0 (or negative) is meaningless as a threshold — the
    burn-rate axis starts at 0. Guard ``typical > 0`` so a defaulted-to-
    zero historical-median doesn't paint a phantom red line at the axis."""
    fig = burn_gauge(_block_with_burn(), typical=0.0)
    assert isinstance(fig, go.Figure)
    threshold = fig.data[0].gauge.threshold
    # Plotly returns an unset threshold as a degenerate object whose
    # `value` is None — that's the "no threshold drawn" shape.
    assert threshold is None or threshold.value is None


def test_burn_gauge_no_burn_rate_returns_none() -> None:
    block = _block_with_burn()
    block = BlockEntry(
        id=block.id,
        startTime=block.start_time,
        endTime=block.end_time,
        actualEndTime=block.actual_end_time,
        isActive=block.is_active,
        isGap=block.is_gap,
        entries=block.entries,
        tokenCounts=block.token_counts,
        totalTokens=block.total_tokens,
        costUSD=block.cost_usd,
        models=block.models,
        burnRate=None,
        projection=None,
    )
    assert burn_gauge(block) is None


# ---------- cache_hit_ratio_line ----------


def test_cache_hit_ratio_line_returns_figure() -> None:
    report = _report(
        [
            _entry("2026-05-15", cost=1.0, model="claude-opus-4-7"),
            _entry("2026-05-16", cost=2.0, model="claude-opus-4-7"),
        ]
    )
    fig = cache_hit_ratio_line(report)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    # 2 points, ratio in [0,1].
    assert len(fig.data[0].x) == 2
    assert all(0.0 <= r <= 1.0 for r in fig.data[0].y)


def test_cache_hit_ratio_line_empty_returns_none() -> None:
    assert cache_hit_ratio_line(_report([])) is None


# ---------- token_flow_sankey ----------


def test_token_flow_sankey_returns_figure() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = token_flow_sankey(report)
    assert isinstance(fig, go.Figure)
    # Single Sankey trace.
    assert len(fig.data) == 1
    trace = fig.data[0]
    # 4 token kinds + 1 family = 5 nodes.
    assert len(trace.node.label) == 5


def test_token_flow_sankey_empty_returns_none() -> None:
    assert token_flow_sankey(_report([])) is None


# ---------- single_family_token_bar ----------


def test_single_family_token_bar_returns_horizontal_log_bar() -> None:
    report = _report([_entry("2026-05-16", cost=1.0, model="claude-opus-4-7")])
    fig = single_family_token_bar(report)
    assert isinstance(fig, go.Figure)
    # Coloured by kind → one trace per category (4 total) so each bar gets
    # a distinct colour from Plotly's qualitative palette.
    assert len(fig.data) == 4
    trace_names = {t.name for t in fig.data}
    assert trace_names == {"cache_read", "cache_create", "output", "input"}
    # Log x so cache_read doesn't drown input.
    assert fig.layout.xaxis.type == "log"
    # Distinct colours: every trace has a different marker color.
    colors = {t.marker.color for t in fig.data if t.marker.color is not None}
    assert len(colors) == 4


def test_single_family_token_bar_empty_returns_none() -> None:
    assert single_family_token_bar(_report([])) is None


def test_single_family_token_bar_all_zero_tokens_returns_none() -> None:
    """A report with entries but every token count == 0 (e.g. cost-only
    accounting glitches) would render four zero-width bars. Bail and
    let the UI render an empty-state caption instead."""
    zero_entry = DailyEntry(
        date="2026-05-16",
        inputTokens=0,
        outputTokens=0,
        cacheCreationTokens=0,
        cacheReadTokens=0,
        totalTokens=0,
        totalCost=0.0,
        modelsUsed=["claude-opus-4-7"],
        modelBreakdowns=[
            ModelBreakdown(
                modelName="claude-opus-4-7",
                inputTokens=0,
                outputTokens=0,
                cacheCreationTokens=0,
                cacheReadTokens=0,
                cost=0.0,
            )
        ],
    )
    assert single_family_token_bar(_report([zero_entry])) is None


# ---------- session_blocks_timeline (slice 17) ----------


def _block(*, block_id: str, start: str, end: str, is_active: bool = False) -> BlockEntry:
    return BlockEntry(
        id=block_id,
        startTime=start,
        endTime=end,
        actualEndTime=None,
        isActive=is_active,
        isGap=False,
        entries=1,
        tokenCounts=BlockTokenCounts(
            inputTokens=10, outputTokens=20,
            cacheCreationInputTokens=30, cacheReadInputTokens=40,
        ),
        totalTokens=100,
        costUSD=1.0,
        models=["claude-opus-4-7"],
        burnRate=None,
        projection=None,
    )


def test_session_blocks_timeline_renders_with_blocks() -> None:
    blocks = [
        _block(block_id="b-1", start="2026-05-16T13:00:00.000Z", end="2026-05-16T18:00:00.000Z"),
        _block(block_id="b-2", start="2026-05-16T19:00:00.000Z", end="2026-05-17T00:00:00.000Z", is_active=True),
    ]
    fig = session_blocks_timeline(blocks)
    assert isinstance(fig, go.Figure)
    # px.timeline emits one trace per colour category (Active/Completed).
    trace_names = {t.name for t in fig.data}
    assert "Active" in trace_names and "Completed" in trace_names


def test_session_blocks_timeline_empty_returns_none() -> None:
    assert session_blocks_timeline([]) is None


def test_session_blocks_timeline_tz_label() -> None:
    blocks = [
        _block(block_id="b-1", start="2026-05-16T13:00:00.000Z", end="2026-05-16T18:00:00.000Z"),
    ]
    fig = session_blocks_timeline(blocks, tz="America/Los_Angeles")
    # The x-axis title shows the user's zone so they're not guessing UTC.
    assert "Los_Angeles" in (fig.layout.xaxis.title.text or "")
