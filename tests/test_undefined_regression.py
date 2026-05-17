"""Regression proof: no `undefined` ever reaches a rendered chart.

The user shipped two Overview chart bugs in successive builds where
the legend rendered a literal `undefined` entry with visible chart
geometry. Root cause: `px.area(color=...)` / `px.bar(color=...)`
auto-generates one trace per category value found in the DataFrame
— including NaN / None / empty-string values, which Plotly's JS
serialiser stringifies to the literal `"undefined"` in legends.

Defensive layers (data → chart → final scrub) eliminate every path
to a phantom trace. This file is the END-TO-END verification:

- AppTest boots the actual `src/tokenscope/app.py` script with the
  same query-param URL the user reproduces against locally.
- The sidebar's `data.daily` call hits real ccusage via the conftest
  fixture, configurable to return either the project's clean
  fixture OR a pathological fixture with edge-case model ids.
- Every rendered `plotly_chart` element is walked from the figure
  tree, its `proto.spec` (the full figure JSON Streamlit ships to
  the JS client) is parsed and searched for the literal string
  `"undefined"`.

Any failure here is the user's exact bug. Pass = the bug is
demonstrably fixed at the rendered-figure level, not just at the
chart-builder unit level.
"""

from __future__ import annotations

import json

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import FIXTURES


APP_PATH = "src/tokenscope/app.py"


def _wire_fixtures(mock_ccusage) -> None:
    """The clean fixture path the existing smoke tests use — base
    case proves the regression is fixed under happy data."""
    mock_ccusage("daily", response=FIXTURES / "daily.json")
    mock_ccusage("daily", "--instances", response=FIXTURES / "daily_by_project.json")
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")
    mock_ccusage("blocks", "--active", response=FIXTURES / "blocks.json")


def _walk_all_strings(obj) -> list[str]:
    """Recursively yield every string value in a JSON-like structure.

    Walks dict values AND dict keys, list/tuple elements. Used by the
    regression test to scan EVERY field in the figure spec — not just
    `trace.name` — so any path that introduces `undefined` (layout
    legend title, annotation text, legendgroup, hovertemplate,
    metadata, etc.) gets caught.
    """
    out: list[str] = []

    def visit(node) -> None:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str):
                    out.append(k)
                visit(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                visit(v)

    visit(obj)
    return out


def _walk_plotly_charts(at: AppTest) -> list[dict]:
    """Walk the AppTest element tree, return one dict per rendered
    `st.plotly_chart` with key, trace names, and a FULL-SPEC string
    walk for offending values.

    The full-spec walk is the broader assertion: every string anywhere
    in the figure JSON (legend.title.text, annotation.text, legendgroup,
    hovertemplate, meta, axis labels, custom_data values, etc.) is
    checked for the literal `"undefined"`. Trace names alone aren't
    sufficient — Plotly's legend can be populated by sources other
    than `trace.name`.
    """
    results: list[dict] = []

    def visit(node) -> None:
        if getattr(node, "type", None) == "plotly_chart":
            spec_raw = node.proto.spec
            spec = json.loads(spec_raw)
            traces = spec.get("data", [])
            parts = node.proto.id.split("-", 2)
            key = parts[2] if len(parts) == 3 else node.proto.id
            # Broadened check: walk every string in the entire figure
            # spec for the literal `undefined`. This is the contract.
            all_strings = _walk_all_strings(spec)
            undefined_hits = [
                s for s in all_strings if "undefined" in s.lower()
            ]
            results.append(
                {
                    "key": key,
                    "trace_count": len(traces),
                    "trace_names": [t.get("name") for t in traces],
                    "any_undefined_in_full_spec": bool(undefined_hits),
                    "undefined_hits": undefined_hits,
                }
            )
        children = getattr(node, "children", None)
        if children:
            iterator = (
                children.values() if hasattr(children, "values") else children
            )
            for child in iterator:
                visit(child)

    visit(at.main)
    return results


def _assert_no_undefined(charts: list[dict]) -> None:
    """Assert every rendered Plotly chart's figure spec is free of the
    literal string `"undefined"`. Failures print the offending string
    values + the chart's trace-name list so the regression is
    debuggable from the test output alone."""
    failures = [c for c in charts if c["any_undefined_in_full_spec"]]
    if failures:
        pytest.fail(
            "rendered figure spec contains `undefined`:\n"
            + "\n".join(
                f"  key={c['key']!r}\n"
                f"    traces={c['trace_names']!r}\n"
                f"    offending strings={c['undefined_hits']!r}"
                for c in failures
            )
        )


# ---------- end-to-end against the user's reproduction URL ----------


@pytest.fixture
def _capture_chart_logs(caplog):
    """Attach caplog directly to `tokenscope.ui.charts` logger.

    The chart module's logger has `propagate=False` so its records
    don't bubble to the root caplog handler by default. Attaching
    caplog's handler directly bridges that gap for this test file.
    """
    import logging as _logging

    target = _logging.getLogger("tokenscope.ui.charts")
    original_level = target.level
    original_handlers = list(target.handlers)
    target.addHandler(caplog.handler)
    target.setLevel(_logging.INFO)
    yield caplog
    target.removeHandler(caplog.handler)
    target.handlers = original_handlers
    target.setLevel(original_level)


def test_overview_at_users_reproduction_url_has_no_undefined(
    mock_ccusage, mock_ccusage_version, _capture_chart_logs
) -> None:
    """The reproduction URL from the user's bug report:

      ?view=overview
       &since=2026-04-18
       &until=2026-05-17
       &models=claude-haiku-4-5-20251001,claude-opus-4-7

    Boots the actual app, renders the Overview, walks every rendered
    `plotly_chart` element, parses its full figure spec, asserts no
    `undefined` appears anywhere in the serialised JSON.

    Also asserts the build-time INFO logs fired so the user can grep
    for `chart.cost_trend.built` / `chart.token_mix.built` in their
    Streamlit stderr and see the trace names that reached Plotly.
    """
    _wire_fixtures(mock_ccusage)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "overview"
    at.query_params["since"] = "2026-04-18"
    at.query_params["until"] = "2026-05-17"
    at.query_params["models"] = "claude-haiku-4-5-20251001,claude-opus-4-7"
    at.run()

    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    assert len(at.error) == 0, [e.value[:200] for e in at.error]

    charts = _walk_plotly_charts(at)
    keys = {c["key"] for c in charts}
    assert "overview-cost-trend" in keys
    assert "overview-token-mix" in keys

    _assert_no_undefined(charts)

    cost_trend = next(c for c in charts if c["key"] == "overview-cost-trend")
    assert "undefined" not in (cost_trend["trace_names"] or [])
    assert None not in (cost_trend["trace_names"] or [])
    assert "" not in (cost_trend["trace_names"] or [])

    token_mix = next(c for c in charts if c["key"] == "overview-token-mix")
    assert set(token_mix["trace_names"]) == {
        "input",
        "output",
        "cache_create",
        "cache_read",
    }

    build_logs = [
        r.message
        for r in _capture_chart_logs.records
        if "chart." in r.message and ".built" in r.message
    ]
    assert any("chart.cost_trend.built" in m for m in build_logs), (
        f"expected `chart.cost_trend.built` log; got: {build_logs!r}"
    )
    assert any("chart.token_mix.built" in m for m in build_logs), (
        f"expected `chart.token_mix.built` log; got: {build_logs!r}"
    )


def test_live_view_renders_with_no_undefined(
    mock_ccusage, mock_ccusage_version, _capture_chart_logs
) -> None:
    """Same end-to-end contract as the Overview reproduction test,
    but for the Live view. Walks every rendered chart's full figure
    spec (spend trajectory + token throughput) and asserts no
    `undefined` appears anywhere.

    Locks the regression at the rendered-figure level for the Live
    view's two charts so any future schema drift or Plotly Express
    auto-trace path that leaks an `undefined` is caught here, not
    only at the chart-builder unit level."""
    _wire_fixtures(mock_ccusage)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "live"
    at.run()

    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    assert len(at.error) == 0, [e.value[:200] for e in at.error]

    charts = _walk_plotly_charts(at)
    keys = {c["key"] for c in charts}
    assert "live-spend-trajectory" in keys
    assert "live-token-mix" in keys
    _assert_no_undefined(charts)

    token_mix = next(c for c in charts if c["key"] == "live-token-mix")
    assert set(token_mix["trace_names"]) == {
        "input", "output", "cache_create", "cache_read"
    }

    build_logs = [
        r.message
        for r in _capture_chart_logs.records
        if "chart." in r.message and ".built" in r.message
    ]
    assert any("chart.live_token_mix.built" in m for m in build_logs), (
        f"expected `chart.live_token_mix.built` log; got: {build_logs!r}"
    )


def test_cache_view_renders_with_no_undefined(
    mock_ccusage, mock_ccusage_version, _capture_chart_logs
) -> None:
    """End-to-end Cache view regression: boots the actual app, walks
    every rendered chart's figure spec (sparkline + reads-vs-writes
    + daily savings + per-model bar), asserts no `undefined` appears
    anywhere. Same defensive contract as the Overview / Live
    regression tests."""
    _wire_fixtures(mock_ccusage)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "cache"
    at.run()

    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    assert len(at.error) == 0, [e.value[:200] for e in at.error]

    charts = _walk_plotly_charts(at)
    keys = {c["key"] for c in charts}
    assert "cache-reads-vs-writes" in keys
    _assert_no_undefined(charts)

    reads_vs_writes = next(
        c for c in charts if c["key"] == "cache-reads-vs-writes"
    )
    assert set(reads_vs_writes["trace_names"]) == {
        "cache_create", "cache_read"
    }

    build_logs = [
        r.message
        for r in _capture_chart_logs.records
        if "chart." in r.message and ".built" in r.message
    ]
    assert any("chart.cache_reads_vs_writes.built" in m for m in build_logs), (
        f"expected `chart.cache_reads_vs_writes.built` log; got: {build_logs!r}"
    )


def test_models_view_renders_with_no_undefined(
    mock_ccusage, mock_ccusage_version, _capture_chart_logs
) -> None:
    """End-to-end Models view regression: boots the actual app,
    walks the per-model token-kind chart's figure spec, asserts no
    `undefined` appears anywhere. Same defensive contract as the
    Overview / Live / Cache regression tests."""
    _wire_fixtures(mock_ccusage)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "models"
    at.run()

    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    assert len(at.error) == 0, [e.value[:200] for e in at.error]

    charts = _walk_plotly_charts(at)
    keys = {c["key"] for c in charts}
    assert "models-token-kind" in keys
    _assert_no_undefined(charts)

    token_kind = next(c for c in charts if c["key"] == "models-token-kind")
    assert set(token_kind["trace_names"]) == {
        "input", "output", "cache_create", "cache_read"
    }

    build_logs = [
        r.message
        for r in _capture_chart_logs.records
        if "chart." in r.message and ".built" in r.message
    ]
    assert any("chart.per_model_token_kind.built" in m for m in build_logs), (
        f"expected `chart.per_model_token_kind.built` log; got: {build_logs!r}"
    )


# ---------- end-to-end against pathological data ----------


_PATHOLOGICAL_DAILY_RESPONSE = {
    "daily": [
        {
            "date": "2026-05-15",
            "inputTokens": 100,
            "outputTokens": 200,
            "cacheCreationTokens": 300,
            "cacheReadTokens": 400,
            "totalTokens": 1000,
            "totalCost": 5.0,
            "modelsUsed": ["claude-opus-4-7"],
            "modelBreakdowns": [
                {
                    "modelName": "claude-opus-4-7",
                    "inputTokens": 100,
                    "outputTokens": 200,
                    "cacheCreationTokens": 300,
                    "cacheReadTokens": 400,
                    "cost": 5.0,
                }
            ],
        },
        # Deprecated family alongside current — model_family classifies
        # correctly to "opus" but the entry tests the path.
        {
            "date": "2026-05-16",
            "inputTokens": 50,
            "outputTokens": 100,
            "cacheCreationTokens": 150,
            "cacheReadTokens": 200,
            "totalTokens": 500,
            "totalCost": 2.0,
            "modelsUsed": ["claude-opus-4-6"],
            "modelBreakdowns": [
                {
                    "modelName": "claude-opus-4-6",
                    "inputTokens": 50,
                    "outputTokens": 100,
                    "cacheCreationTokens": 150,
                    "cacheReadTokens": 200,
                    "cost": 2.0,
                }
            ],
        },
        # Unknown future model id — classifier returns the whole name
        # (non-claude- prefix) or falls into the `model_family` general
        # case. Either way, no None / empty.
        {
            "date": "2026-05-17",
            "inputTokens": 30,
            "outputTokens": 60,
            "cacheCreationTokens": 90,
            "cacheReadTokens": 120,
            "totalTokens": 300,
            "totalCost": 1.5,
            "modelsUsed": ["some-future-anthropic-model-id"],
            "modelBreakdowns": [
                {
                    "modelName": "some-future-anthropic-model-id",
                    "inputTokens": 30,
                    "outputTokens": 60,
                    "cacheCreationTokens": 90,
                    "cacheReadTokens": 120,
                    "cost": 1.5,
                }
            ],
        },
    ],
    "totals": {
        "inputTokens": 180,
        "outputTokens": 360,
        "cacheCreationTokens": 540,
        "cacheReadTokens": 720,
        "totalTokens": 1800,
        "totalCost": 8.5,
    },
}


def test_overview_with_unknown_future_model_id_has_no_undefined(
    mock_ccusage, mock_ccusage_version, _capture_chart_logs
) -> None:
    """The earlier rounds of fixes were insufficient when a model id
    appeared that the classifier didn't anticipate. This test boots
    Overview against a fixture that *includes* such an id and asserts
    the rendered figures still contain zero `undefined`."""
    mock_ccusage("daily", response=_PATHOLOGICAL_DAILY_RESPONSE)
    mock_ccusage(
        "daily",
        "--instances",
        response={
            "projects": {},
            "totals": {
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheCreationTokens": 0,
                "cacheReadTokens": 0,
                "totalTokens": 0,
                "totalCost": 0,
            },
        },
    )
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.query_params["view"] = "overview"
    at.run()

    assert len(at.exception) == 0, [str(e.value)[:200] for e in at.exception]
    assert len(at.error) == 0, [e.value[:200] for e in at.error]

    charts = _walk_plotly_charts(at)
    _assert_no_undefined(charts)

    # The scrubber log fires only when a phantom slipped through —
    # which after these fixes should NEVER happen. Assert it didn't.
    scrub_warnings = [
        r.message
        for r in _capture_chart_logs.records
        if "chart.phantom_trace_scrubbed" in r.message
    ]
    assert not scrub_warnings, (
        f"a phantom trace reached the scrubber: {scrub_warnings!r}"
    )
