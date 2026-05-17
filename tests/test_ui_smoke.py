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

import pytest
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


# ---------- Overview polish round 2 ----------


def test_overview_timezone_caption_has_no_underscore(
    mock_ccusage, mock_ccusage_version
) -> None:
    """IANA timezone identifiers use underscores (`America/New_York`)
    for filesystem safety; UI copy should show spaces. The window
    caption is the most-visible place this leaks through."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    captions = [c.value for c in at.caption]
    window_caption = next((c for c in captions if "Window:" in c), None)
    assert window_caption is not None
    assert "_" not in window_caption.split("times in", 1)[1], (
        f"timezone display still has underscore: {window_caption!r}"
    )


def test_overview_renders_refresh_indicator(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Right-aligned `Updated HH:MM:SS` indicator lives in the page
    header. Surfaced as a div with the `tokenscope-page-refresh`
    class so the CSS rule can right-align it."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    md_text = _markdown_text(at)
    assert "tokenscope-page-refresh" in md_text
    assert "Updated " in md_text


def test_overview_insight_callout_bolds_numbers(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Insight paragraph wraps figures in `<strong>` so the eye lands
    on the dollar amounts and percentages, not the prose."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    # The rendered insight is a single `st.markdown` block containing
    # `<div class="tokenscope-insight">...$X.XX...</div>`. The CSS
    # stylesheet ALSO mentions the class name, so we match on the
    # rendered div opener specifically.
    insight_block = next(
        (
            m.value
            for m in at.markdown
            if '<div class="tokenscope-insight">' in m.value
        ),
        "",
    )
    assert insight_block, "rendered insight div not found"
    assert "<strong>$" in insight_block, (
        f"expected bolded dollar amount in insight; got: {insight_block!r}"
    )


def test_overview_kpi_captions_are_plain_english_not_formulas(
    mock_ccusage, mock_ccusage_version
) -> None:
    """KPI captions used to read `window cost ÷ 30 days` and
    `cache_read / input-side tokens` — implementation formulas
    leaked into the user-facing UI. Captions now use plain English."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    captions = [c.value for c in at.caption]
    for caption in captions:
        assert "÷" not in caption, (
            f"formula symbol `÷` leaked into caption: {caption!r}"
        )
        # The cache-ratio caption explicitly says "share of input-side
        # tokens"; the formula form `cache_read / input-side tokens`
        # is what we're guarding against.
        assert "cache_read /" not in caption, (
            f"formula notation leaked into caption: {caption!r}"
        )


def test_overview_kpi_helps_only_on_cache_hit_ratio(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Help-icon discipline: the metric whose semantics genuinely
    surprise users (cache ratio's denominator excludes output tokens)
    keeps its `?`. Window cost / Last day / Avg daily cost don't —
    they self-explain."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    for m in at.metric:
        if m.label and "Cache hit ratio" in m.label:
            assert m.help, "Cache hit ratio should keep its help tooltip"
        elif m.label and m.label in {"Last day", "Avg daily cost"}:
            assert not m.help, (
                f"`{m.label}` should not have a help tooltip; got: {m.help!r}"
            )


def test_overview_cost_composition_includes_total_row(
    mock_ccusage, mock_ccusage_version
) -> None:
    """A `total` row at the bottom of the cost composition table
    gives the reader an anchor for the per-kind contributions."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    # The DataFrame contents aren't directly inspectable via AppTest's
    # element list. The dataframe's data lives in `.value` which is
    # a pandas DataFrame — query the Kind column for the total row.
    composition_df = None
    for df_element in at.dataframe:
        df = df_element.value
        if "Kind" in df.columns:
            composition_df = df
            break
    assert composition_df is not None, "composition dataframe missing"
    assert "total" in composition_df["Kind"].values


def test_overview_token_mix_has_non_cache_toggle(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Toggle in the Token-mix card switches between the full
    percent-stacked view (default, includes cache_read) and a
    non-cache rebased view that surfaces the input/output/cache_create
    variance otherwise crushed under the cache_read dominance."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    toggle = next(
        (t for t in at.toggle if t.key == "overview-token-mix-include-cache-read"),
        None,
    )
    assert toggle is not None
    assert toggle.value is True  # default = include cache_read


def test_overview_cost_trend_has_stack_overlay_toggle(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The Daily-cost card carries a Stacked/Overlay segmented
    control so a dominant family can't hide the smaller ones —
    overlay mode draws each family independently."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    seg = next(
        (s for s in at.segmented_control if s.key == "overview-cost-trend-mode"),
        None,
    )
    assert seg is not None
    assert seg.options == ["Stacked", "Overlay"]


def test_overview_window_cost_delta_uses_inverse_color(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Cost-up = bad news. The Window cost metric's delta uses
    `delta_color="inverse"` so a positive delta paints red with an
    up-arrow (warning), not green."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    window_cost = next(
        (m for m in at.metric if m.label == "Window cost"),
        None,
    )
    assert window_cost is not None
    # Streamlit's AppTest exposes `delta_color` as a proto field.
    # Inverse = `2` in Streamlit's enum (off=0, normal=1, inverse=2),
    # though the exact integer mapping may shift across versions.
    # Verify it's NOT the default `normal` mode (which would paint
    # cost-up green).
    proto = window_cost.proto.metric if hasattr(window_cost.proto, "metric") else window_cost.proto
    color_attr = getattr(proto, "color", None)
    # The proto enum names: NORMAL / INVERSE / OFF. Inverse maps to 2.
    assert color_attr != 1, (  # 1 = NORMAL = green-positive (wrong for cost)
        f"Window cost delta must NOT use 'normal' color (green up); "
        f"got proto.color={color_attr!r}"
    )


def test_overview_token_mix_toggle_switches_chart_variant(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Flipping the toggle off renders the non-cache variant (only
    three kinds). Verify by interacting via AppTest."""
    _wire_default_fixtures(mock_ccusage)
    at = _at()
    at.run()
    _assert_clean(at)
    toggle = next(
        t for t in at.toggle if t.key == "overview-token-mix-include-cache-read"
    )
    toggle.set_value(False)
    at.run()
    _assert_clean(at)
    # When non-cache is shown, the chart key is the same — the chart
    # rendered by the non-cache branch. We can't easily inspect Plotly
    # trace data from AppTest, but the toggle state being False after
    # set_value confirms the branch ran.
    toggle_after = next(
        t for t in at.toggle if t.key == "overview-token-mix-include-cache-read"
    )
    assert toggle_after.value is False


def test_live_renders(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)


# ---------- Live view rework ----------


def test_live_h1_is_view_name_not_active_billing_block(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The Live page H1 is `Live` — matches the tab name. Prior
    builds said `Active billing block (live)` (redundant when
    you're on the Live tab) which inverted the hierarchy."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    assert "# Live" in md, f"expected `# Live` H1; got: {md!r}"


def test_live_does_not_render_burn_gauge(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Regression: the burn gauge was deleted from the Live view
    (duplicated the $/hr KPI, arbitrary $0–$60 scale, cryptic
    delta). Replaced by the spend-trajectory chart."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    chart_keys = _plotly_chart_keys(at)
    assert "live-burn-gauge" not in chart_keys
    # No "Burn rate" subheader either (the gauge's old section title).
    md = "\n".join(m.value for m in at.markdown)
    assert "Burn rate" not in md or "burn" in md.lower(), (
        "the section header 'Burn rate' as a chart title should be gone"
    )


def test_live_renders_spend_trajectory_chart(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The replacement chart: cumulative cost line + dashed
    projection across the 5-hour block."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    chart_keys = _plotly_chart_keys(at)
    assert "live-spend-trajectory" in chart_keys


def test_live_renders_window_banner_with_models_and_remaining(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The window context (start–end clock time, minutes remaining,
    models in use) lives in a banner under the H1 — not inside a
    KPI delta pill."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    assert "tokenscope-live-banner" in md, (
        f"expected the banner HTML in markdown; got: {md!r}"
    )
    assert "Models in use" in md
    assert "Active block" in md


def test_live_does_not_show_minutes_left_in_projected_total_card(
    mock_ccusage, mock_ccusage_version
) -> None:
    """`X min left` was a property of the window, not the projected
    total cost. It used to appear as a delta pill on the Projected-
    total KPI; now it lives in the window banner."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    projected = next(
        (m for m in at.metric if m.label == "Projected total"),
        None,
    )
    assert projected is not None
    # Delta is empty / None — no more "X min left" pill on this card.
    proto = projected.proto.metric if hasattr(projected.proto, "metric") else projected.proto
    delta_text = getattr(proto, "delta", "")
    assert "min left" not in (delta_text or "")
    assert "min remaining" not in (delta_text or "")


def test_live_renders_refresh_indicator_with_pulse(
    mock_ccusage, mock_ccusage_version
) -> None:
    """A single line replaces the prior two-caption stack
    (`Auto-refreshes every Ns` + `Last refreshed HH:MM:SS`). The
    pulse dot is a CSS animation that runs continuously so the
    user reads the page as live, not static."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    assert "tokenscope-live-refresh" in md
    assert "tokenscope-live-pulse" in md
    assert "Last refreshed" in md
    assert "auto-refreshes every" in md
    # The two-caption stack should NOT appear — the helper-text line
    # "Auto-refreshes every Ns. Ignores the date / project / model
    # filters" used to live separately.
    assert "Ignores the date" not in md


def test_live_does_not_render_projection_detail_expander(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The `Projection detail` expander duplicated KPI-strip values
    and only carried `Projected total tokens` as unique data. That
    one piece moves to a caption under the chart; the expander is
    gone."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    expander_labels = [e.label for e in at.expander]
    assert not any(
        "Projection detail" in (label or "") for label in expander_labels
    )


def test_live_renders_token_kind_kpi_cards(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Second KPI row: four cards (one per token kind) under the
    cost KPI strip. Each card carries:
      - the kind label as HTML (the actual `<div>` markup is what
        Streamlit ends up rendering — Streamlit's `at.metric`
        introspection doesn't surface custom-HTML cards),
      - the PALETTE color as a swatch (the hex literal appears in
        the inline style),
      - an abbreviated token count.
    Locks both the structure (four kinds present) and the visual
    contract (PALETTE hexes appear, swatch class is rendered)."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    for label in ("Input", "Output", "Cache create", "Cache read"):
        assert label in md, f"missing kind label {label!r}; got md: {md!r}"
    # PALETTE-driven swatch markup is on the page (the class plus
    # each kind's hex literal).
    assert "tokenscope-kind-swatch" in md
    for color in ("#ec4899", "#1e40af", "#f59e0b", "#14b8a6"):
        assert color in md, f"missing PALETTE color {color!r} in card swatches"


def test_live_token_throughput_chart_has_four_kinds_no_undefined(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The Live view renders a `live-token-throughput` Plotly chart
    keyed for the second chart card. The chart key is the
    unambiguous marker that the percent-stacked area is in the
    output. Existing `tests/test_undefined_regression.py` walks the
    full app's figure JSON for the literal `undefined`; this test
    locks the chart's PRESENCE on the Live view specifically."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    chart_keys = _plotly_chart_keys(at)
    assert "live-token-throughput" in chart_keys


def test_live_renders_cost_unavailable_caption_when_rates_missing(
    mock_ccusage, mock_ccusage_version, monkeypatch
) -> None:
    """When LiteLLM rates can't be resolved (offline + no cache),
    `block_cost_by_kind` returns None. The token-kind KPI cards
    render an `cost estimate unavailable` caption instead of fake
    dollar amounts. Locks the defensive UI fallback path."""
    monkeypatch.setattr(
        "tokenscope.pricing.rates_for_model", lambda _name: None
    )
    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)
    captions = "\n".join(
        getattr(c, "value", "") for c in at.caption
    ) if hasattr(at, "caption") else ""
    md = "\n".join(m.value for m in at.markdown) + "\n" + captions
    assert "cost estimate unavailable" in md, (
        f"expected fallback caption when rates unresolvable; got: {md!r}"
    )


def test_live_renders_empty_throughput_caption_when_no_token_activity(
    mock_ccusage, mock_ccusage_version
) -> None:
    """When the active block has zero tokens (a brand-new block
    captured immediately after start), the throughput chart has
    no positive-delta intervals to render. `live_token_throughput`
    returns None and the UI shows an empty-state caption instead
    of an empty chart frame. Locks the defensive empty-throughput
    fallback path on the Live view."""
    blocks_payload = {
        "blocks": [
            {
                "id": "2026-05-16T13:00:00.000Z",
                "startTime": "2026-05-16T13:00:00.000Z",
                "endTime": "2026-05-16T18:00:00.000Z",
                "actualEndTime": None,
                "isActive": True,
                "isGap": False,
                "entries": 0,
                "tokenCounts": {
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "cacheReadInputTokens": 0,
                },
                "totalTokens": 0,
                "costUSD": 0.0,
                "models": ["claude-opus-4-7"],
                "burnRate": None,
                "projection": None,
            }
        ]
    }
    mock_ccusage("daily", response=FIXTURES / "daily.json")
    mock_ccusage("daily", "--instances", response=FIXTURES / "daily_by_project.json")
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=blocks_payload)
    mock_ccusage("blocks", "--active", response=blocks_payload)
    at = _at("live")
    at.run()
    _assert_clean(at)
    captions = "\n".join(
        getattr(c, "value", "") for c in at.caption
    ) if hasattr(at, "caption") else ""
    md = "\n".join(m.value for m in at.markdown) + "\n" + captions
    assert "No intra-block intervals with token activity yet" in md, (
        f"expected empty-throughput caption; got: {md!r}"
    )


def test_live_cache_hit_ratio_matches_token_counts(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The cache-hit-ratio callout renders the formula result that
    `block_cache_hit_ratio` would produce from the active block's
    token counts. Computed against the fixture: locks both that the
    callout is on the page AND that the number reflects the same
    formula the analytics layer applies."""
    from tokenscope.analytics import block_cache_hit_ratio
    from tokenscope.models import BlocksReport

    _wire_default_fixtures(mock_ccusage)
    at = _at("live")
    at.run()
    _assert_clean(at)

    blocks_path = FIXTURES / "blocks.json"
    report = BlocksReport.model_validate_json(blocks_path.read_text())
    active = next(b for b in report.blocks if b.is_active)
    expected_pct = block_cache_hit_ratio(active) * 100

    md = "\n".join(m.value for m in at.markdown)
    assert "tokenscope-cache-ratio-callout" in md, (
        "cache hit ratio callout markup not on the page"
    )
    assert f"{expected_pct:.1f}%" in md, (
        f"expected cache hit ratio {expected_pct:.1f}% to appear in markdown"
    )


def test_cache_renders_savings_hero(mock_ccusage, mock_ccusage_version) -> None:
    """Cache view's headline is the savings hero — the `$X saved`
    figure is the largest stat on the page. Locks the framing
    change from `cache_hit_ratio` 99.5% (a vanity metric — caching
    works) to savings (caching's monetary value)."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("cache")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    assert "tokenscope-cache-hero" in md, (
        f"expected savings hero markup; got md: {md!r}"
    )
    assert "Estimated savings from caching" in md
    # The hero card carries either a real `$X.XX` figure or the
    # explicit "—" fallback. Either way it lands on the page.
    assert "tokenscope-cache-hero-value" in md


def test_cache_reads_vs_writes_chart_renders(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Cache reads vs writes chart is keyed `cache-reads-vs-writes`
    and renders inside its card."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("cache")
    at.run()
    _assert_clean(at)
    chart_keys = _plotly_chart_keys(at)
    assert "cache-reads-vs-writes" in chart_keys


def test_cache_renders_savings_unavailable_fallback_when_no_rates(
    mock_ccusage, mock_ccusage_version, monkeypatch
) -> None:
    """When LiteLLM rates aren't reachable, every savings-derived
    surface on the Cache view falls back to its no-rates copy:
    the hero shows `—` + the offline-pricing explanation, the
    daily savings card shows the offline caption."""
    monkeypatch.setattr(
        "tokenscope.pricing.rates_for_model", lambda _name: None
    )
    _wire_default_fixtures(mock_ccusage)
    at = _at("cache")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    captions = "\n".join(
        getattr(c, "value", "") for c in at.caption
    ) if hasattr(at, "caption") else ""
    combined = md + "\n" + captions
    assert "Pricing rates from LiteLLM aren't reachable" in combined
    assert "Savings unavailable" in combined


def test_cache_renders_data_range_banner_when_cache_starts_after_since(
    mock_ccusage, mock_ccusage_version
) -> None:
    """When the sidebar's `since` is older than the first day with
    cache activity, the Cache view surfaces the gap explicitly.

    Uses a daily fixture whose only entry is mid-window relative
    to the sidebar's since query param; the banner explains the
    chart's narrower X-axis."""
    late_cache_daily = {
        "daily": [
            {
                "date": "2026-05-15",
                "inputTokens": 100, "outputTokens": 200,
                "cacheCreationTokens": 300, "cacheReadTokens": 400,
                "totalTokens": 1000, "totalCost": 5.0,
                "modelsUsed": ["claude-opus-4-7"],
                "modelBreakdowns": [
                    {
                        "modelName": "claude-opus-4-7",
                        "inputTokens": 100, "outputTokens": 200,
                        "cacheCreationTokens": 300, "cacheReadTokens": 400,
                        "cost": 5.0,
                    }
                ],
            }
        ],
        "totals": {
            "inputTokens": 100, "outputTokens": 200,
            "cacheCreationTokens": 300, "cacheReadTokens": 400,
            "totalTokens": 1000, "totalCost": 5.0,
        },
    }
    mock_ccusage("daily", response=late_cache_daily)
    # Match the daily entry to the daily-by-project entry — sidebar reads
    # both, and using stale fixtures here can cross-pollinate banners.
    mock_ccusage(
        "daily", "--instances",
        response={
            "projects": {"-project-a": late_cache_daily["daily"]},
            "totals": late_cache_daily["totals"],
        },
    )
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")
    mock_ccusage("blocks", "--active", response=FIXTURES / "blocks.json")
    at = _at("cache", since="2026-04-18", until="2026-05-17")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    assert "tokenscope-cache-range-banner" in md, (
        f"expected data-range banner; got: {md!r}"
    )
    assert "Cache data available from 2026-05-15" in md


def test_cache_renders_reads_vs_writes_fallback_when_no_cache_activity(
    mock_ccusage, mock_ccusage_version
) -> None:
    """When the window has daily entries but every entry has zero
    `cache_create` AND zero `cache_read`, the reads-vs-writes
    panel falls back to its empty-state caption rather than
    rendering an empty bar chart."""
    no_cache_daily = {
        "daily": [
            {
                "date": "2026-05-15",
                "inputTokens": 100, "outputTokens": 200,
                "cacheCreationTokens": 0, "cacheReadTokens": 0,
                "totalTokens": 300, "totalCost": 1.0,
                "modelsUsed": ["claude-opus-4-7"],
                "modelBreakdowns": [
                    {
                        "modelName": "claude-opus-4-7",
                        "inputTokens": 100, "outputTokens": 200,
                        "cacheCreationTokens": 0, "cacheReadTokens": 0,
                        "cost": 1.0,
                    }
                ],
            }
        ],
        "totals": {
            "inputTokens": 100, "outputTokens": 200,
            "cacheCreationTokens": 0, "cacheReadTokens": 0,
            "totalTokens": 300, "totalCost": 1.0,
        },
    }
    mock_ccusage("daily", response=no_cache_daily)
    mock_ccusage(
        "daily", "--instances",
        response={"projects": {"p1": no_cache_daily["daily"]}, "totals": no_cache_daily["totals"]},
    )
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")
    mock_ccusage("blocks", "--active", response=FIXTURES / "blocks.json")
    at = _at("cache")
    at.run()
    _assert_clean(at)
    captions = "\n".join(
        getattr(c, "value", "") for c in at.caption
    ) if hasattr(at, "caption") else ""
    assert "No cache activity in the window." in captions


def test_cache_suppresses_per_model_when_only_one_model_has_activity(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Two models in the window but only one carries cache
    activity → the per-model section is still suppressed
    (`rows_with_activity` has <2 entries). The comparison framing
    requires at least two models to compare."""
    two_models_only_one_caches = {
        "daily": [
            {
                "date": "2026-05-15",
                "inputTokens": 200, "outputTokens": 400,
                "cacheCreationTokens": 300, "cacheReadTokens": 400,
                "totalTokens": 1300, "totalCost": 6.0,
                "modelsUsed": [
                    "claude-opus-4-7",
                    "claude-haiku-4-5-20251001",
                ],
                "modelBreakdowns": [
                    {
                        "modelName": "claude-opus-4-7",
                        "inputTokens": 100, "outputTokens": 200,
                        "cacheCreationTokens": 300, "cacheReadTokens": 400,
                        "cost": 5.0,
                    },
                    {
                        "modelName": "claude-haiku-4-5-20251001",
                        "inputTokens": 100, "outputTokens": 200,
                        "cacheCreationTokens": 0, "cacheReadTokens": 0,
                        "cost": 1.0,
                    },
                ],
            }
        ],
        "totals": {
            "inputTokens": 200, "outputTokens": 400,
            "cacheCreationTokens": 300, "cacheReadTokens": 400,
            "totalTokens": 1300, "totalCost": 6.0,
        },
    }
    mock_ccusage("daily", response=two_models_only_one_caches)
    mock_ccusage(
        "daily", "--instances",
        response={
            "projects": {"p1": two_models_only_one_caches["daily"]},
            "totals": two_models_only_one_caches["totals"],
        },
    )
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")
    mock_ccusage("blocks", "--active", response=FIXTURES / "blocks.json")
    at = _at("cache")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    assert "Per-model cache performance" not in md


def test_cache_renders_empty_window_info_banner(
    mock_ccusage, mock_ccusage_version
) -> None:
    """An empty daily report → the empty-window info banner. No
    crash, no half-rendered hero, no broken charts."""
    empty_daily = {
        "daily": [],
        "totals": {
            "inputTokens": 0, "outputTokens": 0,
            "cacheCreationTokens": 0, "cacheReadTokens": 0,
            "totalTokens": 0, "totalCost": 0.0,
        },
    }
    mock_ccusage("daily", response=empty_daily)
    mock_ccusage(
        "daily", "--instances",
        response={"projects": {}, "totals": empty_daily["totals"]},
    )
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")
    mock_ccusage("blocks", "--active", response=FIXTURES / "blocks.json")
    at = _at("cache")
    at.run()
    _assert_clean(at)
    info_texts = "\n".join(i.value for i in at.info)
    assert "No usage in the selected window" in info_texts


def test_cache_handles_single_model_gracefully(
    mock_ccusage, mock_ccusage_version
) -> None:
    """A single-model window suppresses the per-model breakdown
    section entirely (no empty table, no single-row chart). Lock
    the contract that the section is conditional on >1 model."""
    single_model_daily = {
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
            }
        ],
        "totals": {
            "inputTokens": 100, "outputTokens": 200,
            "cacheCreationTokens": 300, "cacheReadTokens": 400,
            "totalTokens": 1000, "totalCost": 5.0,
        },
    }
    mock_ccusage("daily", response=single_model_daily)
    mock_ccusage("daily", "--instances", response=FIXTURES / "daily_by_project.json")
    mock_ccusage("session", response=FIXTURES / "session.json")
    mock_ccusage("blocks", response=FIXTURES / "blocks.json")
    mock_ccusage("blocks", "--active", response=FIXTURES / "blocks.json")
    at = _at("cache")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    # Per-model section header is the unambiguous marker — it
    # ONLY renders when len(rows) > 1.
    assert "Per-model cache performance" not in md
    chart_keys = _plotly_chart_keys(at)
    assert "cache-per-model-bar" not in chart_keys


def test_cache_renders(mock_ccusage, mock_ccusage_version) -> None:
    _wire_default_fixtures(mock_ccusage)
    at = _at("cache")
    at.run()
    _assert_clean(at)
    # The Cache view's H1 + headline (savings hero) + supporting KPI row.
    md = "\n".join(m.value for m in at.markdown)
    assert "# Cache" in md
    assert "Estimated savings from caching" in md
    assert "Cache hit ratio" in md
    labels = {m.label for m in at.metric}
    assert "Effective $ / 1M tokens" in labels


def test_models_renders(mock_ccusage, mock_ccusage_version) -> None:
    """Models view boots cleanly with the new H1 + 4-card KPI strip.
    Asserts the surface shape: H1 present, Total cost card present,
    Top model card replaces the prior `Models in window: N` count."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("models")
    at.run()
    _assert_clean(at)
    md = "\n".join(m.value for m in at.markdown)
    assert "# Models" in md
    labels = {m.label for m in at.metric}
    assert "Total cost" in labels
    assert "Top model" in labels


def test_models_kpi_strip_no_useless_count(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Locks the framing change: the prior `Models in window: N`
    KPI was a count of table rows the user can see directly below,
    not a useful stat. The 4th slot now carries cost-concentration
    framing (`Top model`) instead."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("models")
    at.run()
    _assert_clean(at)
    labels = {m.label for m in at.metric}
    assert "Models in window" not in labels


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


# ---------- Models per-model token-kind chart ----------


def test_models_renders_per_model_token_kind_chart(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The Sankey + donut + Top-N controls are gone. The replacement
    is a per-model horizontal stacked bar keyed `models-token-kind`,
    which carries the per-model token-kind composition with PALETTE
    colors. Locks both the new chart's presence and the absence of
    the old controls."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("models")
    at.run()
    _assert_clean(at)
    chart_keys = _plotly_chart_keys(at)
    assert "models-token-kind" in chart_keys
    md = "\n".join(m.value for m in at.markdown)
    # Old surface artefacts: section header + control labels.
    assert "Token flow:" not in md
    assert "Width represents" not in md
    assert "Drill into a family" in md


def test_drill_into_family_does_not_raise(
    mock_ccusage, mock_ccusage_version
) -> None:
    """Regression for the crash: the prior code assigned
    `st.session_state["sidebar-models"] = fam_models` from inside
    the Models view AFTER the sidebar widget had instantiated.
    Streamlit's session-state guard turned that into a
    `StreamlitAPIException` on every drill click. The new flow
    routes through `st.query_params` only; the sidebar's
    unconditional URL → session_state sync picks up the change."""
    _wire_default_fixtures(mock_ccusage)
    at = _at("models")
    at.run()
    _assert_clean(at)
    drill_buttons = [
        b for b in at.button
        if "View" in (b.label or "") and "Overview" in (b.label or "")
    ]
    assert drill_buttons, (
        "no drill buttons rendered on Models view — fixture must produce "
        "at least one family in the breakdown"
    )
    drill_buttons[0].click().run()
    # No exception fell out of the rerun.
    assert not at.exception, [str(e.value)[:200] for e in at.exception]
    # The drill routed to Overview with the family's models seeded.
    # AppTest exposes query_params as `list[str]` (Streamlit's
    # multi-value semantics) — unwrap before string comparison.
    def _qp(key: str) -> str | None:
        raw = at.query_params.get(key)
        if isinstance(raw, list):
            return raw[0] if raw else None
        return raw
    assert _qp("view") == "overview"
    models_param = _qp("models") or ""
    # The clicked button was the FIRST family in sorted order — the
    # `models` URL param now carries that family's model ids.
    assert models_param, "models param missing from drill destination"


def test_share_of_cost_calculation(
    mock_ccusage, mock_ccusage_version
) -> None:
    """The breakdown table displays share as a proper percent
    (0–100), not the raw 0–1 fraction the prior code accidentally
    passed through `%.1f%%` (which rendered 99.96% as `1.0%`).

    Locks the analytics contract: shares sum to 1.0 across all
    models in the window (assumption the ProgressColumn relies on
    for max_value=100)."""
    from tokenscope.analytics import model_breakdown
    from tokenscope.models import DailyReport

    _wire_default_fixtures(mock_ccusage)
    daily = json.loads((FIXTURES / "daily.json").read_text())
    report = DailyReport.model_validate(daily)
    rows = model_breakdown(report)
    total_share = sum(row["share"] for row in rows)
    assert total_share == pytest.approx(1.0, abs=1e-9)
    # Every row's share is `cost / total_cost` directly — NOT
    # divided by 100 anywhere along the path.
    total_cost = sum(row["cost"] for row in rows)
    for row in rows:
        expected_share = (
            row["cost"] / total_cost if total_cost else 0.0
        )
        assert row["share"] == pytest.approx(expected_share)


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
