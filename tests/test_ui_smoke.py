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


# ---------- Overview polish: page shell ----------


def _markdown_text(at: AppTest) -> str:
    """Concat all top-level markdown blocks for substring assertions."""
    return "\n".join(m.value for m in at.markdown)


def test_overview_h1_is_view_name_not_product_name(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The Overview page H1 is the view name (`Overview`), not the
    product wordmark. `tokenscope` lives in `page_title` / About menu,
    not as the largest text on every page."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    headings = [h.value for h in at.heading] if hasattr(at, "heading") else []
    # Streamlit AppTest doesn't always expose `# Overview` as an
    # `at.title` element — it renders the markdown # as h1. Search the
    # markdown blocks instead.
    md_text = _markdown_text(at)
    assert "# Overview" in md_text, (
        f"expected `# Overview` H1 in markdown; got: {md_text!r}"
    )


def test_overview_does_not_render_tokenscope_title(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Regression for the inverted-hierarchy issue: the product name
    used to render as `st.title('tokenscope')` and was the biggest
    text on every page. With `st.title` removed, no `at.title` element
    should carry the product wordmark."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    titles = [t.value for t in at.title]
    assert all("tokenscope" not in t for t in titles), (
        f"unexpected tokenscope title element: {titles!r}"
    )


def test_overview_subtitle_carries_window_context(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The caption under the H1 surfaces the window length + timezone
    so the headline numbers are anchored without forcing the user to
    look at the sidebar."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    captions = [c.value for c in at.caption]
    window_caption = next((c for c in captions if "Window:" in c), None)
    assert window_caption is not None, (
        f"expected window-context caption; got: {captions!r}"
    )
    assert "days" in window_caption
    assert "times in" in window_caption.lower()


# ---------- Overview polish: KPI cards ----------


def test_overview_kpi_cards_use_avg_daily_cost_not_active_block(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The KPI row was retired of Active-block $/hr (which belonged on
    Live) in favour of Avg daily cost so every card describes the same
    window."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    labels = {m.label for m in at.metric}
    assert "Avg daily cost" in labels
    assert "Active block $/hr" not in labels


# ---------- Overview polish: insight summary ----------


def test_overview_renders_insight_callout(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The dynamic insight paragraph renders under the KPI strip,
    wrapped in the `tokenscope-insight` CSS class so the eye reads
    it as narrative, not metric."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    md_text = _markdown_text(at)
    assert 'class="tokenscope-insight"' in md_text, (
        f"expected insight callout HTML; got markdown blocks: {md_text!r}"
    )
    # The headline sentence should always include the window total +
    # window length.
    assert "You spent" in md_text


# ---------- Overview polish: cost composition (no expander) ----------


def test_overview_composition_is_inline_not_expander(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Cost composition was buried inside `st.expander`; it's now
    inline as a first-class panel. The H3 `Cost composition` should
    appear in the rendered markdown without being collapsed behind a
    chevron."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    md_text = _markdown_text(at)
    assert "### Cost composition" in md_text, (
        f"expected `### Cost composition` H3; got: {md_text!r}"
    )
    # No expander wraps the composition any more.
    expander_labels = [e.label for e in at.expander]
    assert not any(
        "Cost composition" in (label or "") for label in expander_labels
    ), f"composition is still inside an expander: {expander_labels!r}"


def test_overview_does_not_mention_ccusage_in_visible_copy(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The composition header and subtitle used to reference `ccusage`
    in the visible UI. The product user shouldn't see the upstream
    library's name in their dashboard — it was CLI / developer
    language leaking through."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    # Scan every markdown + caption block on the page.
    visible_strings: list[str] = []
    visible_strings.extend(m.value for m in at.markdown)
    visible_strings.extend(c.value for c in at.caption)
    for s in visible_strings:
        assert "ccusage" not in s.lower(), (
            f"`ccusage` leaked into visible copy: {s!r}"
        )


# ---------- Overview polish: charts ----------


def _plotly_chart_keys(at: AppTest) -> set[str]:
    """Walk the element tree, return every `st.plotly_chart`'s key.

    AppTest doesn't expose `plotly_chart` as a top-level collection;
    it surfaces as an `UnknownElement` with `type == "plotly_chart"`.
    The user-provided key is the trailing segment of `proto.id`,
    formatted as `$$ID-<hash>-<key>` (where the hash may contain
    its own hyphens, so split with maxsplit=2 and take the rest).
    """
    keys: set[str] = set()

    def visit(node) -> None:
        if getattr(node, "type", None) == "plotly_chart":
            parts = node.proto.id.split("-", 2)
            if len(parts) == 3:
                keys.add(parts[2])
        children = getattr(node, "children", None)
        if children:
            iterator = children.values() if hasattr(children, "values") else children
            for child in iterator:
                visit(child)

    visit(at.main)
    return keys


def test_overview_renders_unified_cost_trend_chart(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The Overview's two prior cost charts (`overview-stacked-area`
    + `overview-rolling-line`) consolidated into one `overview-cost-trend`
    that overlays the rolling-average line on top of the stacked area."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    chart_keys = _plotly_chart_keys(at)
    assert "overview-cost-trend" in chart_keys
    assert "overview-token-mix" in chart_keys
    assert "overview-stacked-area" not in chart_keys
    assert "overview-rolling-line" not in chart_keys


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


# ---------- Sidebar polish: removed surfaces ----------


def test_sidebar_no_version_footer(mock_ccusage, mock_ccusage_version) -> None:
    """The `ccusage X.Y.Z` footer was moved out of the sidebar entirely.
    Version surfaces via Streamlit's hamburger About menu instead — the
    sidebar shouldn't carry CLI-tool fingerprints."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    captions = [c.value for c in at.sidebar.caption]
    for caption in captions:
        assert "ccusage" not in caption.lower(), (
            f"unexpected ccusage reference in sidebar caption: {caption!r}"
        )


def test_sidebar_timezone_caption_is_plain_text(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Timezone caption is plain prose — no inline-code backticks
    around the zone identifier, no `TZ` env-var instruction (which
    was CLI documentation leaking into the product UI). README still
    documents the override; the sidebar isn't the place for it."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    tz_caption = next(
        (c.value for c in at.sidebar.caption if "Times shown in" in c.value),
        None,
    )
    assert tz_caption is not None
    assert "`" not in tz_caption, (
        f"timezone caption still contains code-pill backticks: {tz_caption!r}"
    )
    assert "TZ" not in tz_caption, (
        f"timezone caption still mentions the TZ env var: {tz_caption!r}"
    )


# ---------- Sidebar polish: help icons (kept only where they earn it) ----------


def test_sidebar_offline_toggle_keeps_help(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Offline pricing isn't self-explanatory in product language — the
    `?` icon stays on this toggle and its tooltip explains what cached
    pricing means."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    toggle = next(
        (t for t in at.sidebar.toggle if t.label == "Offline pricing"),
        None,
    )
    assert toggle is not None
    assert toggle.help, "Offline pricing toggle should retain its help tooltip"


def test_sidebar_plan_selector_keeps_help(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The plan selector flips the headline KPI from pay-per-token to
    flat-rate; the behavior isn't obvious from `Subscription` alone.
    Help text explains the headline-flip in product language."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    plan = next(
        (s for s in at.sidebar.selectbox if s.label == "Subscription"),
        None,
    )
    assert plan is not None
    assert plan.help, "Subscription selectbox should retain its help tooltip"
    # Tooltip should mention both billing models so the user knows
    # what they're picking between.
    assert "Enterprise" in plan.help and "flat-rate" in plan.help.lower()


def test_sidebar_self_explanatory_widgets_drop_help(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Date range / Project / Models dropped their `?` icons — five
    identical question marks per panel was visual noise, not signal."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    for w in at.sidebar.date_input:
        assert not w.help, f"date_input {w.label!r} retains help={w.help!r}"
    # Project selectbox shouldn't have help; Subscription should.
    project = next(
        s for s in at.sidebar.selectbox if s.label == "Project"
    )
    assert not project.help
    for w in at.sidebar.multiselect:
        assert not w.help, f"multiselect {w.label!r} retains help={w.help!r}"


# ---------- Sidebar polish: added affordances ----------


def test_sidebar_date_preset_chips_present(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Preset chips sit above the date input as a segmented control,
    not as bare buttons — segmented_control gives the brand-accent
    "active preset" affordance for free."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    seg = next(
        (s for s in at.sidebar.segmented_control if s.key == "sidebar-date-preset"),
        None,
    )
    assert seg is not None
    assert seg.options == ["7d", "30d", "MTD", "Custom"]


def test_sidebar_date_preset_7d_seeds_inclusive_seven_day_range(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Selecting the 7d preset fires the `on_change` callback, which
    seeds `_KEY_DATE_RANGE` with an inclusive 7-day range."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    seg = next(
        s for s in at.sidebar.segmented_control if s.key == "sidebar-date-preset"
    )
    seg.set_value("7d")
    at.run()
    _assert_clean(at)
    since, until = at.session_state["sidebar-date-range"]
    assert (until - since).days + 1 == 7


def test_sidebar_date_preset_mtd_starts_on_first_of_month(
    mock_ccusage, mock_ccusage_version
) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    seg = next(
        s for s in at.sidebar.segmented_control if s.key == "sidebar-date-preset"
    )
    seg.set_value("MTD")
    at.run()
    _assert_clean(at)
    since, _until = at.session_state["sidebar-date-range"]
    assert since.day == 1


def test_sidebar_date_preset_custom_is_passive(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Clicking Custom records the choice in segmented-control state
    but leaves `_KEY_DATE_RANGE` untouched — the user is signalling
    "I'll drive the date input directly"."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    # Pre-seed a custom range; verify Custom click doesn't overwrite it.
    from datetime import date as _date

    custom_range = (_date(2026, 3, 1), _date(2026, 3, 15))
    at.session_state["sidebar-date-range"] = custom_range
    at.run()
    seg = next(
        s for s in at.sidebar.segmented_control if s.key == "sidebar-date-preset"
    )
    seg.set_value("Custom")
    at.run()
    _assert_clean(at)
    assert at.session_state["sidebar-date-range"] == custom_range


def test_sidebar_clear_all_models_button_present(
    mock_ccusage, mock_ccusage_version
) -> None:
    """`Clear all` sits beside `Select all` — symmetric batch actions
    for the Models filter."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    labels = {b.label for b in at.sidebar.button}
    assert "Clear all" in labels


def test_sidebar_clear_all_empties_models_selection(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Clicking Clear all sets session_state to `[]` (the empty
    selection) — distinct from Select-all's `pop`, which lets the
    multiselect default re-seed every available model."""
    _wire_default_fixtures(mock_ccusage)
    at = _at(models="claude-opus-4-7")
    at.run()
    _assert_clean(at)
    btn = next(b for b in at.sidebar.button if b.label == "Clear all")
    btn.click()
    at.run()
    _assert_clean(at)
    assert at.session_state["sidebar-models"] == []
    assert "models" not in at.query_params


def test_models_select_all_button_present(mock_ccusage, mock_ccusage_version) -> None:
    """Slice 25: scoped escape hatch sits next to the Models multiselect."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    btn = next(
        (b for b in at.sidebar.button if b.label == "Select all"),
        None,
    )
    assert btn is not None
    assert btn.key == "sidebar-models-select-all"


def test_models_select_all_clears_narrowed_selection(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Slice 25: clicking Select all wipes the narrowed session_state +
    URL param, and the next render re-seeds the multiselect with the
    full default list (every available model)."""
    _wire_default_fixtures(mock_ccusage)
    at = _at(models="claude-opus-4-7")
    # Simulate the user having narrowed to a single model and that
    # narrowing having round-tripped through the URL.
    at.session_state["sidebar-models"] = ["claude-opus-4-7"]
    at.run()
    _assert_clean(at)

    btn = next(b for b in at.sidebar.button if b.label == "Select all")
    btn.click()
    at.run()
    _assert_clean(at)

    # Multiselect must re-render with every available model selected
    # (fixture has at least opus-4-7 and haiku-4-5; both must appear).
    models_widget = next(
        m for m in at.sidebar.multiselect if m.label == "Models"
    )
    assert len(models_widget.value) >= 2
    assert "claude-opus-4-7" in models_widget.value
    assert "claude-haiku-4-5-20251001" in models_widget.value
    # The pre-narrowing URL value must not have survived. The wipe + rerun
    # path means even if `_sync_url_from_session` writes `models=` back,
    # it writes the *full* set, not the prior narrow value. (The
    # absent-when-default behaviour is Slice 26's territory.)
    url_models = at.query_params.get("models")
    if url_models is not None:
        url_list = url_models[0] if isinstance(url_models, list) else url_models
        assert "claude-haiku-4-5-20251001" in url_list


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
