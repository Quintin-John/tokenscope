"""Cache view — savings-first framing with reads/writes + per-model breakdown.

Slice 12 of this view had a `$ saved` headline that multiplied
cache-read tokens by the *full* input rate, which produced
implausibly high savings numbers (every cached read was treated as
"would have been re-sent fresh", which isn't the right
counterfactual). That headline was pulled in favour of the actual
blended rate.

This slice restores the savings framing with the correct formula —
the rate DELTA between input and cache_read — so the savings figure
reflects the discount that caching unlocks on each cached read, not
the hypothetical of every cache_read having been a full input.

Layout (top to bottom):

  * H1 + subtitle.
  * Optional data-range banner — when cache data starts after the
    sidebar window's `since`, surface the explicit start date.
  * Savings hero — large $ saved + the "without caching" comparison.
  * Supporting KPI row — hit ratio (with embedded sparkline),
    effective $/MTok, cache reads, cache writes.
  * Cache reads vs writes by day — stacked bar in a card.
  * Daily savings — bar chart in a card.
  * Per-model cache performance — horizontal stacked bar in a card,
    conditional on >1 model.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from tokenscope.analytics import (
    aggregate_cache_hit_ratio,
    cache_data_range,
    cache_savings,
    format_compact_int,
    per_model_cache_performance,
    window_effective_per_mtok,
)
from tokenscope.models import DailyReport
from tokenscope.navigation import Navigation
from tokenscope.ui._data import load_daily
from tokenscope.ui.charts import (
    cache_hit_sparkline,
    cache_reads_vs_writes_bar,
    daily_cache_savings_bar,
    per_model_cache_bar,
)
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    st.markdown("# Cache")
    st.caption("How much caching is saving you, and where it's working.")

    daily_report = load_daily(state)
    if daily_report is None:
        return

    _render_data_range_banner(daily_report, sidebar_since=state.query.since)

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date "
            "range** in the sidebar, or clearing the **Project** filter "
            "if one is set."
        )
        return

    savings = cache_savings(daily_report)
    _render_savings_hero(savings)
    _render_kpi_row(daily_report, savings=savings)
    _render_reads_vs_writes(daily_report)
    _render_daily_savings(daily_report)
    _render_per_model_performance(daily_report)


# --- data-range banner --------------------------------------------------


def _parse_ccusage_date(raw: str | None) -> date | None:
    """Parse the sidebar's `state.query.since` / `state.query.until`
    back into a `date`.

    The sidebar formats both as ccusage's compact `YYYYMMDD` form
    (driven by `_to_ccusage_date` in `sidebar.py`) so the
    downstream subprocess call is well-formed; for comparison
    against `cache_data_range`'s `YYYY-MM-DD` strings, we have to
    parse it back. Returns ``None`` for missing or malformed
    inputs — the banner is suppressed defensively rather than
    crashing on a surprise format.
    """
    if not raw:
        return None
    if len(raw) == 8 and raw.isdigit():
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _render_data_range_banner(
    daily_report: DailyReport, *, sidebar_since: str | None
) -> None:
    """If the actual cache data range starts AFTER the sidebar's
    selected `since`, surface that gap explicitly.

    The chart's X-axis can only show dates that exist in the data
    — if the user picked Apr 18 → May 17 but cache data only
    starts May 12, the charts read as "5 days of data" against a
    sidebar showing "30 days". Without context, the user is left
    to guess whether caching kicked in part-way through the
    window or there's a bug.

    No banner when the data covers (or exceeds) the sidebar
    window's start date, or when the sidebar's `since` is missing
    or unparseable.
    """
    since_date = _parse_ccusage_date(sidebar_since)
    if since_date is None:
        return
    actual_range = cache_data_range(daily_report)
    if actual_range is None:
        return
    # `cache_data_range` returns `YYYY-MM-DD` strings sourced from
    # `DailyEntry.date`, which is validated by ccusage's JSON
    # schema — no need to guard `fromisoformat` here.
    first_with_cache_date = date.fromisoformat(actual_range[0])
    if first_with_cache_date <= since_date:
        return
    st.markdown(
        f"""
        <div class="tokenscope-cache-range-banner">
          <strong>Cache data available from {actual_range[0]} onward.</strong>
          The sidebar window starts {since_date.isoformat()}, but no
          entry before {actual_range[0]} carries cache activity — the
          charts below reflect the actual cache range, not the full
          sidebar window.
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- savings hero -------------------------------------------------------


def _render_savings_hero(savings: dict | None) -> None:
    """Large `$ saved` headline + the "without caching" comparison.

    The hero is the page's reason for existing — caching's value
    proposition stated in money. When pricing rates aren't
    resolvable (offline + no cache), the panel renders a neutral
    fallback that explains why the figure is missing rather than
    a placeholder $0.
    """
    if savings is None:
        st.markdown(
            """
            <div class="tokenscope-cache-hero">
              <div class="tokenscope-cache-hero-label">
                Estimated savings from caching
              </div>
              <div class="tokenscope-cache-hero-value">—</div>
              <div class="tokenscope-cache-hero-context">
                Pricing rates from LiteLLM aren't reachable right now,
                so the savings calculation can't run. Reconnect or wait
                for the cached pricing snapshot to refresh.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    savings_usd = savings["savings_usd"]
    actual = savings["actual_cost_usd"]
    uncached = savings["uncached_cost_usd"]
    st.markdown(
        f"""
        <div class="tokenscope-cache-hero">
          <div class="tokenscope-cache-hero-label">
            Estimated savings from caching
          </div>
          <div class="tokenscope-cache-hero-value">${savings_usd:,.2f}</div>
          <div class="tokenscope-cache-hero-context">
            Over the selected window. Without caching, your spend would
            have been <strong>${uncached:,.2f}</strong> — you actually
            paid <strong>${actual:,.2f}</strong>. The saving is the rate
            delta on cache_read tokens: (input rate − cache_read rate) ×
            tokens, summed per model.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- KPI row -----------------------------------------------------------


def _render_kpi_row(daily_report: DailyReport, *, savings: dict | None) -> None:
    """Supporting four-card KPI row. Hit ratio card embeds the
    sparkline; the other three are standard metric cards.

    `?` help icons are dropped where the label is self-evident (hit
    ratio, cache reads, cache writes); only the `Effective $/1M`
    card keeps one because the comparison-to-published-rate framing
    isn't obvious from the label alone.
    """
    aggregate = aggregate_cache_hit_ratio(daily_report)
    effective = window_effective_per_mtok(daily_report)
    totals = daily_report.totals

    c1, c2, c3, c4 = st.columns(4)

    with c1, st.container(border=True):
        st.markdown(
            f"""
            <div class="tokenscope-kpi-label">Cache hit ratio</div>
            <div class="tokenscope-kpi-value">{aggregate:.1%}</div>
            """,
            unsafe_allow_html=True,
        )
        spark = cache_hit_sparkline(daily_report)
        if spark is not None:
            st.plotly_chart(
                spark,
                width="stretch",
                key="cache-hit-sparkline",
                config={"displayModeBar": False},
            )
        else:
            st.caption("input-side served from cache")

    with c2, st.container(border=True):
        st.metric(
            "Effective $ / 1M tokens",
            f"${effective:,.3f}" if effective is not None else "—",
            help=(
                "Blended cost per 1M tokens across the window. Compare "
                "to a model's published input rate (~$15/MTok for opus, "
                "$3 for sonnet, $1 for haiku) — caching pulls this down."
            ),
        )
        st.caption("total cost ÷ total tokens")

    with c3, st.container(border=True):
        st.markdown(
            f"""
            <div class="tokenscope-kpi-label">Cache reads</div>
            <div class="tokenscope-kpi-value">
              {format_compact_int(totals.cache_read_tokens)} tokens
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("total served from cache")

    with c4, st.container(border=True):
        st.markdown(
            f"""
            <div class="tokenscope-kpi-label">Cache writes</div>
            <div class="tokenscope-kpi-value">
              {format_compact_int(totals.cache_creation_tokens)} tokens
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("total written to cache")


# --- chart cards --------------------------------------------------------


def _render_reads_vs_writes(daily_report: DailyReport) -> None:
    """Cache reads vs writes per-day stacked bar. Cache_create on
    bottom (amber), cache_read on top (teal) — the bar's overall
    length is the daily cache footprint, the amber slice flags
    write spikes."""
    with st.container(border=True):
        st.markdown("### Cache reads vs writes by day")
        st.caption(
            "When the cache was being built up (writes) vs. paying off "
            "(reads). Healthy caching is mostly reads with periodic "
            "write spikes when new contexts are introduced."
        )
        fig = cache_reads_vs_writes_bar(daily_report)
        if fig is None:
            st.caption("No cache activity in the window.")
            return
        st.plotly_chart(
            fig, width="stretch", key="cache-reads-vs-writes"
        )


def _render_daily_savings(daily_report: DailyReport) -> None:
    """Per-day savings bar chart. Hidden when LiteLLM rates aren't
    resolvable (offline + no cache) — the hero already explains
    the gap, no point repeating it as zero-height bars."""
    with st.container(border=True):
        st.markdown("### Daily savings")
        st.caption(
            "Estimated $ saved each day by caching reads instead of "
            "paying the full input rate."
        )
        fig = daily_cache_savings_bar(daily_report)
        if fig is None:
            st.caption(
                "Savings unavailable — pricing rates from LiteLLM "
                "aren't resolvable in this run."
            )
            return
        st.plotly_chart(
            fig, width="stretch", key="cache-daily-savings"
        )


def _render_per_model_performance(daily_report: DailyReport) -> None:
    """Per-model cache performance. Conditional on >1 model in the
    window — a single-model window has nothing to compare, so the
    section is hidden entirely (no empty table, no single-row chart).

    Renders a horizontal stacked bar (cache_create + cache_read per
    model) PLUS a compact table with the hit ratio, savings, and
    raw token counts the bar doesn't surface.
    """
    rows = per_model_cache_performance(daily_report)
    if rows is None or len(rows) < 2:
        return
    rows_with_activity = [
        r for r in rows
        if r["cache_read_tokens"] > 0 or r["cache_create_tokens"] > 0
    ]
    if len(rows_with_activity) < 2:
        return

    with st.container(border=True):
        st.markdown("### Per-model cache performance")
        st.caption(
            "Cache footprint by model in the selected window. Models "
            "without resolved pricing show savings as `—`."
        )
        fig = per_model_cache_bar(daily_report)
        if fig is not None:
            st.plotly_chart(
                fig, width="stretch", key="cache-per-model-bar"
            )
        table_rows = [
            {
                "Model": r["model"],
                "Cache hit ratio": f"{r['cache_hit_ratio']:.1%}",
                "Reads": format_compact_int(r["cache_read_tokens"]),
                "Writes": format_compact_int(r["cache_create_tokens"]),
                "Savings": (
                    f"${r['savings_usd']:,.2f}" if r["has_rates"] else "—"
                ),
            }
            for r in rows_with_activity
        ]
        st.dataframe(
            table_rows,
            hide_index=True,
            width="stretch",
        )
