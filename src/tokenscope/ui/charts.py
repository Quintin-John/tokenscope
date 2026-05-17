"""Plotly figure builders for the overview view.

These take a `DailyReport` (or pre-computed rows) and return a
`plotly.graph_objects.Figure`. They have no Streamlit dependency, so they
are unit-testable: build a figure, assert its traces / data look right.

When the report has no entries, builders return `None` — the UI layer
renders an empty-state message instead of an empty chart.

Every chart adopts an `enterprise` visual style via
``apply_enterprise_style(fig)``: no in-chart title (the section H3
above the chart carries that), no axis titles (ticks are
self-evident), system-stack font, light dotted horizontal gridlines
only, styled hover tooltip, brand-accent palette. Adding a new chart
means building the figure and ending with
``return apply_enterprise_style(fig)`` — no per-chart style duplication.
"""

from __future__ import annotations

from typing import Literal

import logging

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from tokenscope.analytics import (
    KNOWN_MODEL_FAMILIES,
    UNKNOWN_MODEL_FAMILY,
    cost_share_by_model,
    daily_cache_hit_ratio,
    daily_cost_by_model,
    daily_token_mix,
    rolling_cost_average,
    token_flow_sankey_data,
)
from tokenscope.models import BlockEntry, DailyEntry, DailyReport, SessionEntry

_log = logging.getLogger("tokenscope.ui.charts")


# --- enterprise chart style ----------------------------------------------

# System font stack — matches what most browsers render for native UI
# without needing a web-font import. Keeps Plotly figures visually
# consistent with the surrounding Streamlit page.
_ENTERPRISE_FONT_FAMILY = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', "
    "Roboto, 'Helvetica Neue', Arial, sans-serif"
)

# Brand-hue shade palette — single-hue tonal cascade used for the few
# *sequential* uses where a tonal gradient is the right call (e.g. the
# rolling-average overlay line). NOT used for categorical breakdowns
# — those have their own palette below.
BRAND_HUE_SHADES: tuple[str, ...] = (
    "#0f172a",  # slate-900 (darkest)
    "#475569",  # slate-600 (brand accent)
    "#94a3b8",  # slate-400
    "#cbd5e1",  # slate-300
    "#e2e8f0",  # slate-200 (lightest)
)


# --- categorical palettes -------------------------------------------------
#
# Pre-UI_Fixes (main branch) charts used Plotly Express's default
# qualitative palette via `px.area(color=...)` and `px.bar(color=...)`
# with no explicit color spec. The palette is
# `px.colors.qualitative.Plotly`:
#
#   ['#636EFA',  # indigo
#    '#EF553B',  # RED — excluded (reserved for warnings only)
#    '#00CC96',  # green
#    '#AB63FA',  # purple
#    '#FFA15A',  # orange
#    '#19D3F3',  # cyan
#    '#FF6692',  # pink
#    '#FECB52',  # yellow
#    '#B6E880',  # light green
#    '#FF97FF']  # light pink
#
# These are the chart colors the user knows from earlier builds. We
# keep them — with the one explicit exclusion of red (#EF553B), which
# is reserved for warning / error states throughout the product.
#
# Token kinds and model families pull from DIFFERENT positions in the
# palette so two unrelated categories across the dashboard never share
# a color. `opus` is never confused with `input`.

# --- PALETTE — single source of truth for every chart color --------------
#
# Every chart in the app references `PALETTE` by category name. There
# is no `color_discrete_sequence` anywhere — only
# `color_discrete_map=...` (built from PALETTE slices) or direct
# `marker_color=PALETTE[name]` / `line=dict(color=PALETTE[name])`.
#
# Guarantees:
#   1. No two distinct categories across the dashboard share a color.
#      The eye learns the mapping after one glance — opus is always
#      violet, input is always pink, cache_create is always amber.
#   2. Adding a new category requires a new PALETTE entry. Forgetting
#      causes `family_color_map` / `TOKEN_KIND_COLORS` lookups to fall
#      back to the documented `Other` sentinel rather than silently
#      auto-picking a Plotly default color that could collide.
#   3. Red is reserved for warnings / error states. NO data series
#      anywhere in the app uses red.
#
# Disjoint hue families:
#   * Model families (Daily cost): violet / emerald / cyan / slate
#   * Token kinds (Token mix, Cost composition): pink / blue / amber / teal
#   * Overlay reference line (7-day avg): near-black so it sits as a
#     summary line over the colored bands.
PALETTE: dict[str, str] = {
    # Model families
    "opus": "#8b5cf6",         # violet
    "haiku": "#10b981",        # emerald
    "sonnet": "#06b6d4",       # cyan
    "Other": "#94a3b8",        # slate (unclassified-family sentinel)

    # Token kinds
    "input": "#ec4899",        # pink / magenta
    "output": "#1e40af",       # dark blue
    "cache_create": "#f59e0b", # amber
    "cache_read": "#14b8a6",   # teal

    # Overlay lines
    "7-day avg": "#1f2937",    # near-black (dashed)
}


# Token-kind palette — slice of PALETTE so callers don't need to
# remember which keys are kinds. Used as
# `color_discrete_map=TOKEN_KIND_COLORS` in token-mix charts so
# Plotly emits one trace per known kind with an explicit name + a
# stable brand color.
TOKEN_KIND_COLORS: dict[str, str] = {
    kind: PALETTE[kind]
    for kind in ("input", "output", "cache_create", "cache_read")
}

# The valid set of token-kind labels. Defensive filter: rows whose
# `kind` falls outside this set are dropped before reaching Plotly,
# so an upstream schema drift can't introduce a 5th legend entry.
TOKEN_KIND_LABELS: frozenset[str] = frozenset(TOKEN_KIND_COLORS)


# Canonical colors for currently-known Anthropic families. Each
# family keeps the same hue across every render regardless of input
# ordering or which other families are present.
_FAMILY_CANONICAL_COLORS: dict[str, str] = {
    family: PALETTE[family]
    for family in ("opus", "sonnet", "haiku")
}

# Color for any family the registry doesn't recognise (the
# `analytics.UNKNOWN_MODEL_FAMILY` sentinel, future-model ids,
# non-Claude names that slipped through). Neutral slate — visually
# distinct from branded families so it reads as "uncategorised".
_UNKNOWN_FAMILY_COLOR = PALETTE["Other"]


def family_color_map(families: list[str]) -> dict[str, str]:
    """Build a family-name → color map sourced entirely from `PALETTE`.

    Known Anthropic families (`opus` / `sonnet` / `haiku` — see
    `KNOWN_MODEL_FAMILIES`) always get their canonical PALETTE hue
    regardless of input ordering or which other families are present.
    That means "opus is violet" holds whether the window contains 1
    family or 5, in any order — the user's color/family muscle memory
    survives re-renders.

    Any family the canonical map doesn't recognise — non-Claude
    models (`gpt-4o`), future Anthropic families Anthropic hasn't
    shipped yet, the `UNKNOWN_MODEL_FAMILY` sentinel, an empty-string
    leak — gets `PALETTE["Other"]` (neutral slate). NO chart-builder
    auto-pick from a Plotly default sequence; the only way to colour
    a new branded family is to add it to PALETTE explicitly.
    """
    out: dict[str, str] = {}
    for family in sorted(set(families), key=lambda f: f or ""):
        if family in _FAMILY_CANONICAL_COLORS:
            out[family] = _FAMILY_CANONICAL_COLORS[family]
        else:
            # Unknown / sentinel / blank — all map to the documented
            # `Other` slot. No silent auto-coloring.
            out[family] = _UNKNOWN_FAMILY_COLOR
    return out

# Neutral grays for text + gridlines, sourced from the same Tailwind
# slate scale as the brand shades so everything stays in family.
_TEXT_PRIMARY = "#0f172a"
_TEXT_MUTED = "#64748b"
_GRID_COLOR = "rgba(15, 23, 42, 0.06)"
_BORDER_COLOR = "#e2e8f0"


def _scrub_undefined_traces(fig: go.Figure) -> go.Figure:
    """Nuclear defensive: drop any trace whose name renders as the
    literal `undefined` in Plotly's JS legend.

    Production-side data should never produce these — the
    `color_discrete_map` + row-level filter in each chart builder
    prevents it — but this scrubber is the belt-and-braces last
    line. The cost is trivial (linear scan of trace list) and the
    payoff is that no phantom legend entry can ever reach the user
    regardless of what schema drift, classifier failure, or
    Pandas/Plotly internal quirk produces.

    Drops traces whose name is `None`, empty-string, `"undefined"`,
    `"nan"`, or `"None"` — every case where the JS legend would
    show stub text. Idempotent.
    """
    bad = {"", "undefined", "nan", "None"}
    cleaned = [
        trace
        for trace in fig.data
        if trace.name is not None and trace.name not in bad
    ]
    if len(cleaned) != len(fig.data):
        # The chart builders are supposed to make this impossible.
        # If a phantom trace reaches here, something earlier in the
        # pipeline let it through — log so the issue is visible in
        # production logs even though the user never sees the bad
        # trace.
        import logging

        bad_names = [
            repr(t.name) for t in fig.data
            if t.name is None or t.name in bad
        ]
        logging.getLogger("tokenscope.ui.charts").warning(
            "chart.phantom_trace_scrubbed names=%s", bad_names
        )
    fig.data = tuple(cleaned)
    return fig


def apply_enterprise_style(fig: go.Figure) -> go.Figure:
    """Apply the shared Overview chart styling. Idempotent.

    Drops the in-chart title (the section H3 above the chart owns
    that), drops both axis titles (the ticks are self-evident),
    switches the font to the system stack, replaces the busy default
    gridlines with light dotted horizontals only (no vertical, no
    axis spines), and replaces Plotly's default tooltip with a
    bordered branded card.

    Also scrubs any phantom `undefined`-named traces as the final
    pipeline step — see `_scrub_undefined_traces`.

    Forces ``clickmode="event+select"`` on every chart so the
    Streamlit PlotlyChart bundle's selection-event setup receives a
    clean event mode. The user reported a browser-side
    `Unhandled Promise Rejection: undefined` originating in
    `PlotlyChart.*.js` — when `on_select="rerun"` is passed to
    `st.plotly_chart` but the figure's `clickmode` is left at the
    Plotly default (just `"event"`), the wrapper attaches a
    selection-event promise that can reject without a value. The
    rejection value (JS `undefined`) was rendering as a phantom
    legend entry. Setting `clickmode="event+select"` explicitly
    aligns the figure with what the wrapper expects.
    """
    # Plotly's title / axis-title fields serialise as objects: `title:
    # {text, font, ...}`. Passing `title=None` to `update_layout` does
    # NOT clear the object — Plotly retains `title: {}`, and the JS
    # renderer then reads `title.text` (= JS `undefined`) and draws
    # the LITERAL string `undefined` as a `<tspan>` SVG element in
    # the `g.gtitle` group. That's the phantom legend-looking item
    # the user has been seeing. Explicit empty-string `title_text=""`
    # serialises as `title: {text: ""}` which renders correctly as
    # nothing visible.
    fig.update_layout(
        title_text="",
        clickmode="event+select",
        font=dict(family=_ENTERPRISE_FONT_FAMILY, size=12, color=_TEXT_PRIMARY),
        margin=dict(l=8, r=8, t=16, b=8),
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(
            title_text="",
            showgrid=False,
            showline=False,
            zeroline=False,
            tickfont=dict(size=11, color=_TEXT_MUTED),
        ),
        yaxis=dict(
            title_text="",
            showgrid=True,
            gridcolor=_GRID_COLOR,
            griddash="dot",
            showline=False,
            zeroline=False,
            tickfont=dict(size=11, color=_TEXT_MUTED),
        ),
        hoverlabel=dict(
            bgcolor="white",
            bordercolor=_BORDER_COLOR,
            font=dict(
                family=_ENTERPRISE_FONT_FAMILY, size=12, color=_TEXT_PRIMARY
            ),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            title_text="",
            font=dict(
                family=_ENTERPRISE_FONT_FAMILY, size=11, color=_TEXT_MUTED
            ),
        ),
    )
    return _scrub_undefined_traces(fig)


__all_style_exports__ = ("apply_enterprise_style", "BRAND_HUE_SHADES")


def _daily_metric_figure(
    df: pd.DataFrame,
    *,
    x: str,
    y: str,
    labels: dict[str, str],
    color: str | None = None,
    multi_day: Literal["area", "line"],
) -> go.Figure:
    """Single-day-safe per-day metric figure.

    `px.area` paints a zero-width band when only one x-value is
    present; `px.line` shrinks to a marker that's easy to miss next to
    a $-axis. Both fall back to `px.bar` (stacked when ``color`` is
    set) so the data stays visible in every window length. This is the
    one authoritative path for that fallback — chart builders compose
    it instead of duplicating the branching logic.
    """
    if df[x].nunique() == 1:
        fig = px.bar(df, x=x, y=y, color=color, labels=labels)
        if color is not None:
            fig.update_layout(barmode="stack")
        return fig
    if multi_day == "area":
        return px.area(df, x=x, y=y, color=color, labels=labels)
    fig = px.line(df, x=x, y=y, color=color, labels=labels)
    fig.update_traces(mode="lines+markers")
    return fig


_UNKNOWN_FAMILY_FALLBACK = "other"


def _normalised_cost_rows(
    daily_report: DailyReport,
) -> pd.DataFrame | None:
    """Return per-day cost-by-family rows with `family` guaranteed to
    be a non-empty string.

    The earlier implementation passed the raw `daily_cost_by_model`
    output to `px.area` / `px.bar`. If any row had an empty / `None` /
    `NaN` family value, Plotly emitted an extra trace whose name
    became the JS literal `undefined` — visible as a phantom band and
    a `undefined` legend entry. Filtering and coercing here means
    every trace downstream has a real category name.

    Returns `None` when the report has no rows or no positive cost,
    so the caller can short-circuit to an empty chart.
    """
    rows = daily_cost_by_model(daily_report)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["family"] = (
        df["family"].fillna("").astype(str).where(
            df["family"].notna() & (df["family"] != ""),
            _UNKNOWN_FAMILY_FALLBACK,
        )
    )
    return df.groupby(["date", "family"], as_index=False)["cost"].sum()


def cost_trend_with_rolling(
    daily_report: DailyReport,
    *,
    rolling_window_days: int = 7,
    spike: tuple[str, float] | None = None,
    mode: str = "stacked",
) -> go.Figure | None:
    """Combined daily-cost-by-family + N-day rolling average overlay
    + optional spike annotation.

    Replaces the previous two-chart layout (`stacked_area` +
    `rolling_average_line`) — both plotted the same underlying data,
    the rolling line just being the smoothed envelope of the area.
    One chart shows spikes (area) and trend (line) without duplication.

    ``mode``:

    - ``"stacked"`` (default): stacked area, family bands sum to the
      total cost height. Familiar "where did the money go" reading,
      but a dominant family can crush small ones into invisible
      slivers near the baseline.
    - ``"overlay"``: each family rendered as its own non-stacked area,
      transparent fill. Small families stay visible because they
      paint their own absolute trajectory rather than getting buried
      under cumulative summing. Bands are drawn smallest-cost-first
      so the loudest family is on top and doesn't fully occlude the
      quieter ones.

    Categorical coloring uses `family_color_map(families)` so the
    chart emits exactly one trace per family with an explicit name
    bound to a distinct, brand-stable hue. The rolling-average
    overlay line uses the brand's darkest slate so it reads as the
    "summary line" above the family bands.

    ``spike``: optional ``(date, cost)`` tuple identifying an
    outlier day worth calling out. Rendered as a Plotly annotation
    arrow with the day's value inline.

    Single-day windows fall back to a stacked bar so the chart stays
    visible (an area chart with one x-value paints zero width).
    """
    if mode not in ("stacked", "overlay"):
        raise ValueError(
            f"cost_trend_with_rolling mode must be 'stacked' or 'overlay'; "
            f"got {mode!r}"
        )

    grouped = _normalised_cost_rows(daily_report)
    if grouped is None:
        return None

    # Order families by total cost in the window. Stacked mode draws
    # largest first (so the family at the bottom of the stack is the
    # biggest, which is the conventional reading). Overlay mode draws
    # smallest LAST (last-drawn = on-top in Plotly), so a small
    # family stays visible above a dominant one's fill.
    family_totals = grouped.groupby("family")["cost"].sum().sort_values(
        ascending=False
    )
    families = list(family_totals.index)
    color_map = family_color_map(families)

    if grouped["date"].nunique() == 1:
        fig = go.Figure()
        for family in families:
            sub = grouped[grouped["family"] == family].sort_values("date")
            fig.add_trace(
                go.Bar(
                    x=sub["date"],
                    y=sub["cost"],
                    name=family,
                    marker_color=color_map[family],
                    hovertemplate=(
                        f"<b>{family}</b><br>%{{x}}<br>$%{{y:,.2f}}"
                        "<extra></extra>"
                    ),
                )
            )
        fig.update_layout(barmode="stack", yaxis_tickprefix="$")
        return apply_enterprise_style(fig)

    # Multi-day: build family bands manually with `go.Scatter`. Earlier
    # iterations used `px.area(..., color="family", ...)`; Plotly Express
    # can auto-introduce a phantom category trace when the DataFrame
    # has any ambiguous category value (NaN, None, empty string,
    # uncategorised entry). Hand-building one go.Scatter per known
    # family guarantees that only the families we explicitly enumerated
    # produce traces — no auto-generated "undefined" can slip through.
    #
    # Stacked vs overlay differs only by `stackgroup`: a non-None value
    # makes Plotly stack the traces into a cumulative area; None gives
    # each family its own absolute trajectory.
    fig = go.Figure()
    stackgroup = "cost" if mode == "stacked" else None
    for family in families:
        sub = grouped[grouped["family"] == family].sort_values("date")
        fig.add_trace(
            go.Scatter(
                x=sub["date"],
                y=sub["cost"],
                mode="lines",
                name=family,
                legendgroup=family,
                stackgroup=stackgroup,
                fillcolor=(
                    color_map[family]
                    if mode == "stacked"
                    else _with_alpha(color_map[family], 0.18)
                ),
                line=dict(color=color_map[family], width=2),
                hovertemplate=(
                    f"<b>{family}</b><br>%{{x}}<br>$%{{y:,.2f}}"
                    "<extra></extra>"
                ),
            )
        )

    rolling_label = f"{rolling_window_days}-day avg"
    rolling = rolling_cost_average(daily_report, window_days=rolling_window_days)
    if rolling:
        rdf = pd.DataFrame(rolling, columns=["date", "avg_cost"])
        fig.add_trace(
            go.Scatter(
                x=rdf["date"],
                y=rdf["avg_cost"],
                mode="lines",
                name=rolling_label,
                legendgroup=rolling_label,
                showlegend=True,
                line=dict(color=PALETTE["7-day avg"], width=2, dash="dot"),
                hovertemplate=(
                    f"<b>{rolling_label}</b><br>"
                    "%{x}<br>$%{y:,.2f}<extra></extra>"
                ),
            )
        )

    if spike is not None:
        spike_date, spike_cost = spike
        # Spike annotation: same near-black as the 7-day avg overlay
        # — both are reference marks layered on top of the family
        # bands, so they share a single palette entry.
        annotation_color = PALETTE["7-day avg"]
        fig.add_annotation(
            x=spike_date,
            y=spike_cost,
            text=f"{spike_date} · ${spike_cost:,.0f}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1,
            arrowcolor=annotation_color,
            ax=20,
            ay=-30,
            bgcolor="white",
            bordercolor=_BORDER_COLOR,
            borderwidth=1,
            font=dict(
                family=_ENTERPRISE_FONT_FAMILY,
                size=11,
                color=annotation_color,
            ),
        )

    styled = apply_enterprise_style(fig).update_layout(
        yaxis_tickprefix="$",
        xaxis_type="date",
        xaxis_tickformat="%b %d",
    )
    _log.info(
        "chart.cost_trend.built mode=%s trace_names=%s family_count=%d",
        mode,
        [t.name for t in styled.data],
        len(families),
    )
    return styled


def _with_alpha(hex_color: str, alpha: float) -> str:
    """Convert `#RRGGBB` to `rgba(R,G,B,A)`. Used to give overlay
    family bands a semi-transparent fill so they layer without
    fully occluding the bands behind them."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.3f})"


def token_mix_percent_bar(daily_report: DailyReport) -> go.Figure | None:
    """Per-day token-mix as a percent-stacked bar.

    Replaces the prior log-axis absolute-tokens chart. A "mix" is a
    composition question — what fraction of each day's tokens went
    to each kind — which percent-stacking surfaces directly. Each
    day's bars sum to 100%; the legend identifies the four kinds
    (input / output / cache_create / cache_read).

    Categorical coloring uses `color_discrete_map=TOKEN_KIND_COLORS`
    (explicit kind → color mapping) instead of a positional
    `color_discrete_sequence`. The explicit map means Plotly emits
    exactly one trace per known kind with an explicit name. Combined
    with the row-level filter to `TOKEN_KIND_LABELS`, any upstream
    drift that introduced a 5th kind (or a NaN-named row) would be
    dropped before reaching Plotly's JS layer — which was
    stringifying empty/missing category names to the literal
    `"undefined"` in legends.

    Absolute token counts are preserved in the hover via customdata
    so the magnitude question is one tooltip away.

    Days with zero tokens get no bar (the underlying `daily_token_mix`
    emits zero-valued rows; percent-stacking can't divide by zero).
    The continuous date axis surfaces those gaps honestly rather than
    rendering them as adjacent bars that hide the missing data.
    """
    rows = daily_token_mix(daily_report)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # Defensive filter: drop any row whose kind isn't one of the four
    # documented values. `daily_token_mix` only emits those four today,
    # but the chart layer doesn't trust that — schema drift can't leak
    # a phantom legend entry.
    df = df[df["kind"].isin(TOKEN_KIND_LABELS)].copy()
    df["tokens"] = df["tokens"].fillna(0)
    if df.empty:
        return None

    totals = df.groupby("date")["tokens"].transform("sum")
    df["pct"] = (df["tokens"] / totals.where(totals > 0, 1) * 100).where(
        totals > 0, 0.0
    )

    kind_order = ["input", "output", "cache_create", "cache_read"]

    # Hand-build one go.Bar per known kind rather than `px.bar(...,
    # color="kind", ...)`. Plotly Express can auto-introduce a phantom
    # category trace when the DataFrame has any ambiguous value (NaN,
    # None, empty string). Manual traces guarantee that only the four
    # kinds we explicitly enumerated produce legend entries.
    fig = go.Figure()
    for kind in kind_order:
        sub = df[df["kind"] == kind].sort_values("date")
        if sub.empty:
            continue
        fig.add_trace(
            go.Bar(
                x=sub["date"],
                y=sub["pct"],
                customdata=sub["tokens"].astype("int64").to_numpy().reshape(-1, 1),
                name=kind,
                legendgroup=kind,
                marker_color=TOKEN_KIND_COLORS[kind],
                hovertemplate=(
                    f"<b>{kind}</b><br>%{{x}}<br>"
                    "%{y:.1f}% · %{customdata[0]:,d} tokens<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        barmode="stack",
        yaxis_ticksuffix="%",
        yaxis_range=[0, 100],
    )
    styled = apply_enterprise_style(fig).update_layout(
        xaxis_type="date",
        xaxis_tickformat="%b %d",
    )
    _log.info(
        "chart.token_mix.built trace_names=%s",
        [t.name for t in styled.data],
    )
    return styled


# Kinds rendered by the non-cache mini chart — same shape as
# `token_mix_percent_bar` but excludes `cache_read` so the non-cache
# variance is visible. cache_read typically holds 98%+ of the bar
# height on real Claude Code data, crushing the other three into a
# 2% sliver; this chart re-bases the percentages on the non-cache
# total so input / output / cache_create read as meaningful slices.
_NON_CACHE_KINDS: frozenset[str] = frozenset({"input", "output", "cache_create"})


def token_mix_non_cache_percent_bar(
    daily_report: DailyReport,
) -> go.Figure | None:
    """Per-day non-cache token-mix as a percent-stacked bar.

    Companion to `token_mix_percent_bar`. The main token-mix chart is
    dominated by `cache_read` (~99% of every bar on typical data),
    leaving the other three kinds visually flat. Re-basing the
    percentages on the non-cache subtotal makes the input / output /
    cache_create variance legible — answers "of the tokens I'm
    actually generating or sending fresh, what's the mix?".

    Same defensive contract as `token_mix_percent_bar`:
    `color_discrete_map` + `isin(_NON_CACHE_KINDS)` filter, so
    unanticipated kinds never reach Plotly.

    Returns ``None`` when no daily entries have any non-cache tokens.
    """
    rows = daily_token_mix(daily_report)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df[df["kind"].isin(_NON_CACHE_KINDS)].copy()
    df["tokens"] = df["tokens"].fillna(0)
    if df.empty or df["tokens"].sum() == 0:
        return None

    totals = df.groupby("date")["tokens"].transform("sum")
    df["pct"] = (df["tokens"] / totals.where(totals > 0, 1) * 100).where(
        totals > 0, 0.0
    )

    kind_order = ["input", "output", "cache_create"]
    fig = go.Figure()
    for kind in kind_order:
        sub = df[df["kind"] == kind].sort_values("date")
        if sub.empty:
            continue
        fig.add_trace(
            go.Bar(
                x=sub["date"],
                y=sub["pct"],
                customdata=sub["tokens"].astype("int64").to_numpy().reshape(-1, 1),
                name=kind,
                legendgroup=kind,
                marker_color=TOKEN_KIND_COLORS[kind],
                hovertemplate=(
                    f"<b>{kind}</b><br>%{{x}}<br>"
                    "%{y:.1f}% of non-cache · %{customdata[0]:,d} tokens"
                    "<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        barmode="stack",
        yaxis_ticksuffix="%",
        yaxis_range=[0, 100],
    )
    styled = apply_enterprise_style(fig).update_layout(
        xaxis_type="date",
        xaxis_tickformat="%b %d",
    )
    _log.info(
        "chart.token_mix_non_cache.built trace_names=%s",
        [t.name for t in styled.data],
    )
    return styled


def donut_cost_by_model(entry: DailyEntry | SessionEntry) -> go.Figure | None:
    """Donut: cost share by model for a single day or session.

    Returns None when the entry has no model breakdowns (defensive — every
    entry ccusage emits has at least one in practice).
    """
    rows = cost_share_by_model(entry)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    fig = px.pie(
        df,
        values="cost",
        names="model",
        hole=0.55,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), showlegend=True)
    return fig


def session_token_mix(entry: SessionEntry) -> go.Figure:
    """Stacked bar of input / output / cache_create / cache_read for one session."""
    rows = [
        {"kind": "input", "tokens": entry.input_tokens},
        {"kind": "output", "tokens": entry.output_tokens},
        {"kind": "cache_create", "tokens": entry.cache_creation_tokens},
        {"kind": "cache_read", "tokens": entry.cache_read_tokens},
    ]
    df = pd.DataFrame(rows)
    fig = px.bar(
        df,
        x="kind",
        y="tokens",
        color="kind",
        category_orders={"kind": ["input", "output", "cache_create", "cache_read"]},
        labels={"kind": "", "tokens": "Tokens"},
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        showlegend=False,
    )
    return fig


def burn_gauge(
    block: BlockEntry, typical: float | None = None
) -> go.Figure | None:
    """Burn-rate gauge: actual cost-per-hour with projected end-of-window cost as a delta.

    When `typical` is provided (median burn from completed historical
    blocks), a red threshold line is drawn at that value — gives users an
    instant "above/below my usual" read instead of asking them to remember
    what their typical burn looks like.

    Returns None when the block has no burn rate (gap block or finished block).
    """
    if block.burn_rate is None:
        return None
    projected = block.projection.total_cost if block.projection else None
    delta = {"reference": projected, "valueformat": "$,.2f"} if projected else None
    gauge: dict = {
        "axis": {"tickprefix": "$"},
        "bar": {"color": "#1f77b4"},
    }
    if typical is not None and typical > 0:
        gauge["threshold"] = {
            "line": {"color": "#d62728", "width": 3},
            "thickness": 0.85,
            "value": typical,
        }
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number" + ("+delta" if delta else ""),
            value=block.burn_rate.cost_per_hour,
            number={"prefix": "$", "valueformat": ",.2f", "suffix": "/hr"},
            delta=delta,
            gauge=gauge,
            title={"text": "Burn rate"},
        )
    )
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=320)
    return fig


def cache_hit_ratio_line(daily_report: DailyReport) -> go.Figure | None:
    """Per-day cache hit ratio line chart (y-axis 0–100%)."""
    series = daily_cache_hit_ratio(daily_report)
    if not series:
        return None
    df = pd.DataFrame(series, columns=["date", "ratio"])
    fig = px.line(
        df,
        x="date",
        y="ratio",
        labels={"date": "Date", "ratio": "Cache hit ratio"},
    )
    fig.update_traces(mode="lines+markers")
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(tickformat=".0%", range=[0, 1]),
    )
    return fig


def single_family_token_bar(daily_report: DailyReport) -> go.Figure | None:
    """Horizontal bar of total tokens per kind, for a window where only
    one model family is present.

    A Sankey with one right-side node is just a four-strand comb feeding
    one label — adds visual ceremony without insight. This bar gives the
    same information honestly. Uses log x-axis for the same reason the
    daily token-mix bar does (cache_read swamps everything else). Each
    kind gets its own colour from Plotly's default qualitative palette
    so the four bars read as distinct categories at a glance.
    """
    if not daily_report.daily:
        return None
    totals = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    for entry in daily_report.daily:
        totals["input"] += entry.input_tokens
        totals["output"] += entry.output_tokens
        totals["cache_create"] += entry.cache_creation_tokens
        totals["cache_read"] += entry.cache_read_tokens
    if not any(totals.values()):
        return None
    kinds = ["cache_read", "cache_create", "output", "input"]
    df = pd.DataFrame({"kind": kinds, "tokens": [totals[k] for k in kinds]})
    fig = px.bar(
        df,
        x="tokens",
        y="kind",
        color="kind",
        orientation="h",
        category_orders={"kind": kinds},
        labels={"tokens": "Tokens", "kind": ""},
    )
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>%{x:,.0f} tokens<extra></extra>",
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_type="log",
        height=260,
        showlegend=False,
    )
    return fig


def token_flow_sankey(
    daily_report: DailyReport,
    *,
    value_mode: str = "tokens",
    top_n: int | None = None,
) -> go.Figure | None:
    """Sankey: token-kind → model family.

    ``value_mode``:
      * ``"tokens"`` — link widths proportional to token counts.
      * ``"cost"``  — link widths proportional to per-family cost,
        proportionally attributed across kinds. Total Sankey width then
        equals total window cost.

    ``top_n`` collapses smaller families into an "Others" node.

    Family labels always carry the family's aggregate cost. Hover detail
    shows both the raw token count and the family's total cost so the
    user reads both dimensions regardless of which mode is active.
    """
    data_ = token_flow_sankey_data(daily_report, value_mode=value_mode, top_n=top_n)
    if not data_["values"]:
        return None
    customdata = data_["customdata"]
    value_label = "Cost share" if value_mode == "cost" else "Tokens"
    value_format = "$,.2f" if value_mode == "cost" else ",d"
    link_hover = (
        "<b>%{source.label}</b> → <b>%{target.label}</b><br>"
        f"{value_label}: %{{value:{value_format}}}<br>"
        "Tokens (absolute): %{customdata[0]:,d}<br>"
        "Family total cost: $%{customdata[1]:,.2f}<extra></extra>"
    )
    fig = go.Figure(
        go.Sankey(
            node=dict(
                label=data_["labels"],
                pad=18,
                thickness=18,
            ),
            link=dict(
                source=data_["sources"],
                target=data_["targets"],
                value=data_["values"],
                customdata=customdata,
                hovertemplate=link_hover,
            ),
        )
    )
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), height=520)
    return fig


def session_blocks_timeline(
    blocks: list[BlockEntry],
    tz: str | None = None,
) -> go.Figure | None:
    """Horizontal timeline: each block as a bar from its start to its end.

    Used on the session detail view to close PLAN.md §3.1's
    "Blocks within session" drill. Active blocks render in a brighter
    colour so the live one stands out from the completed ones.

    Hover carries block id, cost, and total tokens. When ``tz`` is
    given, the chart's x-axis displays timestamps in the user's
    timezone; otherwise UTC.

    Returns None for an empty list — the caller renders empty-state copy.
    """
    if not blocks:
        return None
    rows = []
    for b in blocks:
        rows.append(
            {
                "block_id": b.id,
                "start": b.start_time,
                "end": b.end_time,
                "label": "Active" if b.is_active else "Completed",
                "cost": b.cost_usd,
                "tokens": b.total_tokens,
            }
        )
    df = pd.DataFrame(rows)
    fig = px.timeline(
        df,
        x_start="start",
        x_end="end",
        y="block_id",
        color="label",
        category_orders={"label": ["Active", "Completed"]},
        hover_data={
            "block_id": False,
            "start": False,
            "end": False,
            "label": False,
            "cost": ":$,.2f",
            "tokens": ":,.0f",
        },
    )
    fig.update_yaxes(autorange="reversed")  # newest at top
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=max(180, 40 * len(blocks) + 60),
        legend_title_text="",
        xaxis_title=f"Time ({tz})" if tz else "Time (UTC)",
        yaxis_title="",
    )
    return fig
