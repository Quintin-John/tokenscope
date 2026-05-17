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
# Earlier slices used `BRAND_HUE_SHADES` as a `color_discrete_sequence` for
# every multi-series chart. That collapsed categorical data (model
# families, token kinds — unrelated buckets) into a tonal cascade that
# made adjacent series indistinguishable. Categorical data needs
# distinguishable HUES, not shades of one.
#
# The palette below is a Stripe/Vercel-inspired 5-color set. Red is
# deliberately absent — reserved for warnings throughout the product.
# Hues were chosen for distinguishability under both normal vision and
# common color-vision deficiencies; pure brightness contrast also
# carries the difference for users with monochrome displays.

_CATEGORICAL_PALETTE: tuple[str, ...] = (
    "#4F46E5",  # indigo-600
    "#0891B2",  # cyan-600
    "#059669",  # emerald-600
    "#D97706",  # amber-600
    "#DB2777",  # pink-600
)


# Stable mapping from token-kind label → color. Used as `color_discrete_map`
# in `token_mix_percent_bar` so Plotly emits exactly one trace per kind
# with an explicit name — no phantom `undefined` traces from data-driven
# enumeration.
TOKEN_KIND_COLORS: dict[str, str] = {
    "input": _CATEGORICAL_PALETTE[0],
    "output": _CATEGORICAL_PALETTE[2],
    "cache_create": _CATEGORICAL_PALETTE[3],
    "cache_read": _CATEGORICAL_PALETTE[1],
}

# The valid set of token-kind labels. Defensive filter: rows whose `kind`
# falls outside this set are dropped before reaching Plotly, so an
# upstream schema drift can't introduce a 5th legend entry.
TOKEN_KIND_LABELS: frozenset[str] = frozenset(TOKEN_KIND_COLORS)


# Canonical brand colors for the currently-known Anthropic model
# families. Each family keeps the same hue across every render
# regardless of which other families happen to be in the window —
# users build muscle memory ("opus is the indigo band"). Pulled from
# `_CATEGORICAL_PALETTE` so the brand colors stay coordinated; the
# specific index per family is the assignment.
_FAMILY_CANONICAL_COLORS: dict[str, str] = {
    "opus": _CATEGORICAL_PALETTE[0],     # indigo — most-capable
    "sonnet": _CATEGORICAL_PALETTE[1],   # cyan
    "haiku": _CATEGORICAL_PALETTE[2],    # emerald
}

# Color reserved for the "I couldn't classify this family" sentinel
# (`analytics.UNKNOWN_MODEL_FAMILY`). Neutral slate so the band reads
# as "not categorised" rather than competing with the branded
# Anthropic-family colors.
_UNKNOWN_FAMILY_COLOR = "#94a3b8"  # slate-400


def family_color_map(families: list[str]) -> dict[str, str]:
    """Build a family-name → color map with brand-stable assignments.

    Known Anthropic families (`opus` / `sonnet` / `haiku` — see
    `KNOWN_MODEL_FAMILIES`) always get their canonical hue regardless
    of input ordering or which other families are present. That means
    "opus is indigo" holds whether the window contains 1 family or 5,
    in any order — the user's color/family muscle memory survives
    re-renders.

    Unknown families (non-Claude models like `gpt-4o`, future Claude
    families Anthropic hasn't shipped yet, or the
    `UNKNOWN_MODEL_FAMILY` sentinel) get colors from the remainder of
    the categorical palette. The unknown sentinel itself gets a
    neutral gray — visually distinct from branded families so it
    reads as "uncategorised", not as a peer category.

    Stable under input reordering (the result is a dict; the colors
    are deterministic per family name).
    """
    fallback_pool = [
        c for c in _CATEGORICAL_PALETTE if c not in _FAMILY_CANONICAL_COLORS.values()
    ]
    fallback_index = 0
    out: dict[str, str] = {}
    # Sort the unknown families so successive renders see a
    # deterministic assignment from the fallback pool.
    for family in sorted(set(families), key=lambda f: f or ""):
        if family == UNKNOWN_MODEL_FAMILY:
            out[family] = _UNKNOWN_FAMILY_COLOR
        elif family in _FAMILY_CANONICAL_COLORS:
            out[family] = _FAMILY_CANONICAL_COLORS[family]
        elif not family:
            # Defensive: an empty-string family slipped through the
            # `model_family` fallback (shouldn't happen post-fix, but
            # the chart layer doesn't trust upstream).
            out[family] = _UNKNOWN_FAMILY_COLOR
        else:
            out[family] = fallback_pool[fallback_index % max(len(fallback_pool), 1)]
            fallback_index += 1
    return out

# Neutral grays for text + gridlines, sourced from the same Tailwind
# slate scale as the brand shades so everything stays in family.
_TEXT_PRIMARY = "#0f172a"
_TEXT_MUTED = "#64748b"
_GRID_COLOR = "rgba(15, 23, 42, 0.06)"
_BORDER_COLOR = "#e2e8f0"


def apply_enterprise_style(fig: go.Figure) -> go.Figure:
    """Apply the shared Overview chart styling. Idempotent.

    Drops the in-chart title (the section H3 above the chart owns
    that), drops both axis titles (the ticks are self-evident),
    switches the font to the system stack, replaces the busy default
    gridlines with light dotted horizontals only (no vertical, no
    axis spines), and replaces Plotly's default tooltip with a
    bordered branded card.
    """
    fig.update_layout(
        title=None,
        font=dict(family=_ENTERPRISE_FONT_FAMILY, size=12, color=_TEXT_PRIMARY),
        margin=dict(l=8, r=8, t=16, b=8),
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(
            title=None,
            showgrid=False,
            showline=False,
            zeroline=False,
            tickfont=dict(size=11, color=_TEXT_MUTED),
        ),
        yaxis=dict(
            title=None,
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
    return fig


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
) -> go.Figure | None:
    """Combined daily-cost-by-family stacked area + N-day rolling
    average overlay + optional spike annotation.

    Replaces the previous two-chart layout (`stacked_area` +
    `rolling_average_line`) — both plotted the same underlying data,
    the rolling line just being the smoothed envelope of the area.
    One chart shows spikes (area) and trend (line) without duplication.

    Categorical coloring uses `family_color_map(families)` so the
    chart emits exactly one trace per family with an explicit name
    bound to a distinct hue. The rolling-average overlay line uses
    the brand's darkest slate shade so it reads as the "summary
    line" sitting on top of the colored bands.

    ``spike``: optional ``(date, cost)`` tuple identifying an outlier
    day worth calling out. Rendered as a Plotly annotation arrow with
    the day's value inline. Computed by `analytics.spike_day` — kept
    out of this builder so the threshold logic stays in analytics.

    Single-day windows fall back to a stacked bar so the chart stays
    visible (an area chart with one x-value paints zero width).
    """
    grouped = _normalised_cost_rows(daily_report)
    if grouped is None:
        return None

    families = sorted(grouped["family"].unique())
    color_map = family_color_map(families)

    if grouped["date"].nunique() == 1:
        fig = px.bar(
            grouped,
            x="date",
            y="cost",
            color="family",
            color_discrete_map=color_map,
            category_orders={"family": families},
        )
        fig.update_layout(barmode="stack", yaxis_tickprefix="$")
        return apply_enterprise_style(fig)

    fig = px.area(
        grouped,
        x="date",
        y="cost",
        color="family",
        color_discrete_map=color_map,
        category_orders={"family": families},
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
                line=dict(color=_TEXT_PRIMARY, width=2, dash="dot"),
                hovertemplate=(
                    f"<b>{rolling_label}</b><br>"
                    "%{x}<br>$%{y:,.2f}<extra></extra>"
                ),
            )
        )

    if spike is not None:
        spike_date, spike_cost = spike
        fig.add_annotation(
            x=spike_date,
            y=spike_cost,
            text=f"{spike_date} · ${spike_cost:,.0f}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1,
            arrowcolor=_TEXT_PRIMARY,
            ax=20,
            ay=-30,
            bgcolor="white",
            bordercolor=_BORDER_COLOR,
            borderwidth=1,
            font=dict(
                family=_ENTERPRISE_FONT_FAMILY, size=11, color=_TEXT_PRIMARY
            ),
        )

    return apply_enterprise_style(fig).update_layout(
        yaxis_tickprefix="$",
        xaxis_type="date",
        xaxis_tickformat="%b %d",
    )


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
    # documented values. The upstream emitter (`daily_token_mix`) only
    # produces those four today, but the chart layer doesn't trust
    # that — schema drift can't leak a phantom legend entry.
    df = df[df["kind"].isin(TOKEN_KIND_LABELS)].copy()
    df["tokens"] = df["tokens"].fillna(0)
    if df.empty:
        return None

    totals = df.groupby("date")["tokens"].transform("sum")
    df["pct"] = (df["tokens"] / totals.where(totals > 0, 1) * 100).where(
        totals > 0, 0.0
    )

    kind_order = ["input", "output", "cache_create", "cache_read"]
    fig = px.bar(
        df,
        x="date",
        y="pct",
        color="kind",
        color_discrete_map=TOKEN_KIND_COLORS,
        category_orders={"kind": kind_order},
        custom_data=["tokens"],
    )
    fig.update_traces(
        hovertemplate=(
            "<b>%{fullData.name}</b><br>%{x}<br>"
            "%{y:.1f}% · %{customdata[0]:,d} tokens<extra></extra>"
        ),
    )
    fig.update_layout(
        barmode="stack",
        yaxis_ticksuffix="%",
        yaxis_range=[0, 100],
    )
    return apply_enterprise_style(fig).update_layout(
        xaxis_type="date",
        xaxis_tickformat="%b %d",
    )


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
    fig = px.bar(
        df,
        x="date",
        y="pct",
        color="kind",
        color_discrete_map=TOKEN_KIND_COLORS,
        category_orders={"kind": kind_order},
        custom_data=["tokens"],
    )
    fig.update_traces(
        hovertemplate=(
            "<b>%{fullData.name}</b><br>%{x}<br>"
            "%{y:.1f}% of non-cache · %{customdata[0]:,d} tokens"
            "<extra></extra>"
        ),
    )
    fig.update_layout(
        barmode="stack",
        yaxis_ticksuffix="%",
        yaxis_range=[0, 100],
    )
    return apply_enterprise_style(fig).update_layout(
        xaxis_type="date",
        xaxis_tickformat="%b %d",
    )


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
