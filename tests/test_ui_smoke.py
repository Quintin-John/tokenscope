"""End-to-end smoke tests via streamlit.testing.v1.AppTest.

Unit tests can't reach the UI modules (overview / cache / models / live
/ day / session / block / sidebar / breadcrumbs / app) because they're
built from `st.*` calls that need a real Streamlit runtime. `AppTest`
provides that runtime.

These tests use the `mock_ccusage` fixture (see `conftest.py`) so no
subprocess runs — the ccusage shell-out is patched to serve fixture
JSON instead. Fast, deterministic, doesn't depend on whatever happens
to live in `~/.claude` on the developer's machine.

Production code is unchanged; the patch lives in the fixture's scope.
Live-ccusage integration coverage is in `test_ccusage_live.py`,
opt-in via `pytest -m integration`.
"""

from __future__ import annotations

import json

from streamlit.testing.v1 import AppTest

from tests.conftest import FIXTURES


APP_PATH = "src/tokenscope/app.py"


def _at(view: str | None = None, **params: str) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    if view:
        at.query_params["view"] = view
    for k, v in params.items():
        at.query_params[k] = v
    return at


def _assert_clean(at: AppTest) -> None:
    """Render must produce no Python exceptions and no streamlit errors."""
    assert len(at.exception) == 0, [str(e.value)[:300] for e in at.exception]
    assert len(at.error) == 0, [e.value[:300] for e in at.error]


def _wire_default_fixtures(mock_ccusage) -> None:
    """Register the standard set of fixture responses used by most tests.

    Daily / daily-by-project / session / blocks — every read path the UI
    might trigger. Individual tests can override with more-specific mocks
    after this is set up.
    """
    mock_ccusage("daily", response=FIXTURES / "daily.json")
    mock_ccusage("daily", "--instances", response=FIXTURES / "daily_by_project.json")
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")
    mock_ccusage("blocks", "--active", response=FIXTURES / "blocks.json")


# ---------- top-level views ----------


def test_overview_renders(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    labels = {m.label for m in at.metric}
    assert any(l.startswith("Window cost") for l in labels)


def test_live_renders(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)


def test_cache_renders(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("cache")
    at.run()
    _assert_clean(at)
    labels = {m.label for m in at.metric}
    assert "Cache hit ratio (window)" in labels
    assert "Effective rate ($ / 1M tokens)" in labels


def test_models_renders(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("models")
    at.run()
    _assert_clean(at)
    labels = {m.label for m in at.metric}
    assert "Total cost" in labels
    assert "Models in window" in labels


# ---------- drill views ----------


def test_day_renders_with_valid_day(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    daily = json.loads((FIXTURES / "daily.json").read_text())
    day = daily["daily"][0]["date"]  # pick a real fixture date
    at = _at("day", day=day)
    at.run()
    _assert_clean(at)


def test_day_with_no_day_param_shows_back_affordance(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Slice 11 regression: `?view=day` (no `day`) must show an exit."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("day")
    at.run()
    _assert_clean(at)
    back_buttons = [b for b in at.button if "Overview" in (b.label or "")]
    assert back_buttons


def test_drill_view_does_not_revert_when_page_selector_has_stale_state(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Regression: the page selector's `st.session_state["top-page-selector"]`
    persists across renders. After the user interacts with the selector
    (e.g. clicks "Overview" to leave a drill), session_state holds
    "Overview". On the NEXT drill (e.g. clicking a chart day), Streamlit
    resurrects "Overview" ahead of the `index=None` argument and the
    page-selector handler reroutes the user back out of the drill.

    Diagnosed via the logging slice:
        chart.drill chart=overview-token-mix raw='2026-04-24'
        nav.route target=Navigation(view='day', ...)
        app.render view=day        ← drill succeeded
        app.render view=overview   ← reverted by stale page-selector

    Fix: pop `top-page-selector` from session_state before rendering
    the radio whenever the current view is not a top-level view.
    """
    _wire_default_fixtures(mock_ccusage)
    at = _at("day", day="2026-04-05")
    # Simulate the user having previously picked "Overview" via the
    # page-selector — exactly the state that triggered the bug.
    at.session_state["top-page-selector"] = "Overview"
    at.run()
    _assert_clean(at)
    # The user-facing proof of fix: URL stays on the drill view. Pre-fix
    # the radio would resurrect "Overview", `chosen_view != nav.view`,
    # query_params get cleared and rewritten to `view=overview`, and
    # st.rerun fires. The fix pops the stale session_state so the radio
    # sees index=None and returns None → no reroute.
    #
    # AppTest exposes query_params with multidict semantics; values come
    # back as lists. Compare the first entry.
    view = at.query_params["view"]
    day = at.query_params["day"]
    assert (view[0] if isinstance(view, list) else view) == "day"
    assert (day[0] if isinstance(day, list) else day) == "2026-04-05"


def test_day_renders_session_and_block_rows_via_shared_helper(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Slice 5 regression: _session_row and _block_row both compose
    _entity_row, which must emit:
      - one Open-session button keyed `open-session-<id>`
      - one Open-block button keyed `open-block-<id>`
    on a day where the session.json and blocks.json fixtures overlap.

    The shared helper is correct iff the buttons appear with the
    expected key prefixes for both entity types.
    """
    _wire_default_fixtures(mock_ccusage)
    # 2026-04-05 is the first overlap date in the fixtures (1 session,
    # 4 blocks all starting on that date).
    at = _at("day", day="2026-04-05")
    at.run()
    _assert_clean(at)

    button_keys = [b.key for b in at.button if b.key]
    session_buttons = [k for k in button_keys if k.startswith("open-session-")]
    block_buttons = [k for k in button_keys if k.startswith("open-block-")]
    assert len(session_buttons) >= 1, (
        f"expected at least one 'open-session-*' button; got keys={button_keys}"
    )
    assert len(block_buttons) >= 1, (
        f"expected at least one 'open-block-*' button; got keys={button_keys}"
    )


def test_session_renders_with_valid_id(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    session = json.loads((FIXTURES / "session.json").read_text())
    sid = session["sessions"][0]["sessionId"]
    at = _at("session", session=sid)
    at.run()
    _assert_clean(at)
    labels = {m.label for m in at.metric}
    assert "Cost" in labels


def test_session_without_session_param(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("session")
    at.run()
    _assert_clean(at)


def test_block_renders_with_valid_id(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    blocks = json.loads((FIXTURES / "blocks.json").read_text())
    bid = next(b["id"] for b in blocks["blocks"] if not b["isGap"])
    at = _at("block", block=bid)
    at.run()
    _assert_clean(at)
    labels = {m.label for m in at.metric}
    assert "Cost so far" in labels


def test_block_without_block_param(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("block")
    at.run()
    _assert_clean(at)


# ---------- routing edge cases ----------


def test_invalid_view_falls_back_to_overview(
    mock_ccusage, mock_ccusage_version
) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("drop-tables")
    at.run()
    _assert_clean(at)


def test_page_selector_visible_on_drill_views(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Slice 11 invariant: top page selector renders on every view."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("day", day="2026-05-16")
    at.run()
    radios = [r for r in at.radio if r.key == "top-page-selector"]
    assert radios


# ---------- sidebar behaviours ----------


def test_reset_filters_button_present(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    btn = next((b for b in at.sidebar.button if b.label == "Reset filters"), None)
    assert btn is not None


def test_plan_switch_to_pro_flips_window_cost_kpi(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Slice 12: Pro plan → Window cost = prorated fee with 'at API rates' delta."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    plan = next(s for s in at.sidebar.selectbox if s.label == "Subscription")
    plan.set_value("Pro")
    at.run()
    _assert_clean(at)
    pro_metric = next(
        (m for m in at.metric if m.label and m.label.startswith("Plan cost (Pro)")),
        None,
    )
    assert pro_metric is not None
    assert pro_metric.delta is not None and "API" in pro_metric.delta


# ---------- empty-data branches ----------


def test_overview_handles_no_data(mock_ccusage, mock_ccusage_version) -> None:
    """Default empty fixture responses → views must show empty-state copy
    rather than crashing."""
    at = _at()
    at.run()
    _assert_clean(at)


def test_cache_handles_no_data(mock_ccusage, mock_ccusage_version) -> None:
    at = _at("cache")
    at.run()
    _assert_clean(at)


def test_models_handles_no_data(mock_ccusage, mock_ccusage_version) -> None:
    at = _at("models")
    at.run()
    _assert_clean(at)


def test_live_handles_no_active_block(mock_ccusage, mock_ccusage_version) -> None:
    """No active block in the mock response → empty-state info banner."""
    at = _at("live")
    at.run()
    _assert_clean(at)


# ---------- Sankey controls (slice 13) ----------


def test_models_renders_multi_family_sankey(
    mock_ccusage, mock_ccusage_version
) -> None:
    """When the fixture has multiple families, Models renders the Sankey
    with the width-mode segmented control."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("models")
    at.run()
    _assert_clean(at)
    # Fixture has both opus and haiku, so Sankey + controls render.
    markdown = [m.value for m in at.markdown]
    assert any("Token flow:" in m for m in markdown)


# ---------- ccusage bare `[]` coercion (slice 13 bug fix) ----------


def test_overview_handles_bare_array_from_ccusage(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Regression for the ManageLiterature crash. When ccusage returns a
    bare ``[]`` for an empty-range query (e.g. prior-period fetch for a
    project that has no historical data), `_coerce_empty` must normalise
    it to the expected dict shape — overview must render without a
    pydantic ValidationError.
    """
    mock_ccusage("daily", response=[])
    at = _at()
    at.run()
    _assert_clean(at)
