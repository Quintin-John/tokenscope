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
from tokenscope.plans import Plan
from tokenscope.ui._data import load_daily
from tokenscope.ui.charts import (
    cache_hit_sparkline,
    cache_reads_vs_writes_bar,
    daily_cache_savings_bar,
    per_model_cache_bar,
)
from tokenscope.ui.sidebar import SidebarState


# --- plan-aware copy (single source of truth) ---------------------------
#
# Caching delivers different value on different plans:
#   * Enterprise / pay-per-token: real dollars saved (cache_read tokens
#     billed at a fraction of the input rate).
#   * Flat-rate (Pro / Max 5× / Max 20×): the dollar figure is the
#     API-equivalent value caching adds to throughput — NOT money out
#     of pocket. The user pays the fixed monthly fee regardless.
#
# Every plan-aware string the Cache view renders lives in these
# module-private helpers, keyed off `plan.is_flat_rate`. The render
# functions never embed plan-keyed literals inline — they call the
# helpers. Branching is always on `plan.is_flat_rate`, never on
# `plan.name`.


def _page_caption(plan: Plan) -> str:
    """One-line subtitle under the `# Cache` H1. Names the user's
    actual billing reality: flat-rate users see "API-equivalent;
    your plan covers actual billing"; Enterprise sees the simpler
    "saving you money" framing because that's literally true."""
    if plan.is_flat_rate:
        return (
            "How much value caching adds to your throughput, and where "
            "it's working. Costs are API-equivalent; your plan covers "
            "actual billing."
        )
    return "How much caching is saving you, and where it's working."


def _hero_label(plan: Plan) -> str:
    """Label inside the savings hero. Flat-rate gets the explicit
    `API-equivalent` qualifier so the dollar figure beneath it
    isn't mistaken for money out of pocket."""
    if plan.is_flat_rate:
        return "API-equivalent savings from caching"
    return "Estimated savings from caching"


def _hero_savings_context_html(
    plan: Plan,
    *,
    savings_usd: float,
    actual: float,
    uncached: float,
) -> str:
    """Inner HTML for the savings-hero context div when LiteLLM
    rates ARE available. Flat-rate names the API-equivalent semantics
    AND the flat-fee decoupling — the user must understand the
    dollar isn't a real saving from their pocket."""
    if plan.is_flat_rate:
        return (
            f"Over the selected window, caching saved an estimated "
            f"<strong>${savings_usd:,.2f}</strong> in API-equivalent "
            f"costs (you'd have paid <strong>${uncached:,.2f}</strong> "
            f"at API rates without caching, vs <strong>${actual:,.2f}"
            f"</strong> with). Your plan's monthly fee is fixed "
            "regardless — this is the value caching adds to your "
            "throughput budget, not money out of pocket."
        )
    return (
        f"Over the selected window. Without caching, your spend would "
        f"have been <strong>${uncached:,.2f}</strong> — you actually "
        f"paid <strong>${actual:,.2f}</strong>. The saving is the rate "
        "delta on cache_read tokens: (input rate − cache_read rate) × "
        "tokens, summed per model."
    )


def _hero_no_rates_context(plan: Plan) -> str:
    """Inner text for the savings-hero context div when LiteLLM
    rates are NOT reachable (savings is None). Flat-rate
    additionally notes that the plan fee is unchanged — the user
    isn't losing money to the pricing outage."""
    if plan.is_flat_rate:
        return (
            "Pricing rates from LiteLLM aren't reachable right now, "
            "so the API-equivalent savings calculation can't run. "
            "Your plan's monthly fee is unchanged regardless."
        )
    return (
        "Pricing rates from LiteLLM aren't reachable right now, "
        "so the savings calculation can't run. Reconnect or wait "
        "for the cached pricing snapshot to refresh."
    )


def _effective_rate_label(plan: Plan) -> str:
    """Metric label for the Effective $/MTok KPI. Flat-rate gets
    the `API-equivalent` qualifier so the rate isn't mistaken for
    what the user actually pays per MTok (they pay the fixed
    monthly fee)."""
    if plan.is_flat_rate:
        return "API-equivalent $ / 1M tokens"
    return "Effective $ / 1M tokens"


def _effective_rate_caption(plan: Plan) -> str:
    """One-line caption beneath the Effective $/MTok metric."""
    if plan.is_flat_rate:
        return "API-equivalent cost ÷ total tokens"
    return "total cost ÷ total tokens"


def _effective_rate_help(plan: Plan) -> str:
    """Help-icon tooltip for the Effective $/MTok KPI. Flat-rate
    adds the flat-fee decoupling note."""
    base = (
        "Blended cost per 1M tokens across the window. Compare "
        "to a model's published input rate (~$15/MTok for opus, "
        "$3 for sonnet, $1 for haiku) — caching pulls this down."
    )
    if plan.is_flat_rate:
        return (
            f"API-equivalent {base[0].lower()}{base[1:]} "
            "Your plan's monthly fee is fixed regardless."
        )
    return base


def _daily_savings_title(plan: Plan) -> str:
    """Section header above the daily-savings bar chart."""
    if plan.is_flat_rate:
        return "### Daily API-equivalent savings"
    return "### Daily savings"


def _daily_savings_caption(plan: Plan) -> str:
    """Body caption beneath the daily-savings chart section.
    Flat-rate names the API-equivalent framing AND the flat-fee
    decoupling."""
    if plan.is_flat_rate:
        return (
            "Estimated API-equivalent $ saved each day by caching "
            "reads instead of paying the full input rate at API "
            "rates. Your plan's monthly fee is fixed regardless."
        )
    return (
        "Estimated $ saved each day by caching reads instead of "
        "paying the full input rate."
    )


def _per_model_caption(plan: Plan) -> str:
    """Body caption beneath the `Per-model cache performance`
    section header. Flat-rate names the API-equivalent framing
    AND the flat-fee decoupling alongside the existing
    `models without resolved pricing show —` note."""
    if plan.is_flat_rate:
        return (
            "Cache footprint by model in the selected window. "
            "Per-model savings figures are API-equivalent; your "
            "plan's monthly fee is fixed regardless. Models without "
            "resolved pricing show `—`."
        )
    return (
        "Cache footprint by model in the selected window. Models "
        "without resolved pricing show savings as `—`."
    )


def _per_model_savings_column_header(plan: Plan) -> str:
    """Column header for the per-model table's savings column.
    Enterprise reads `Savings` (real money); flat-rate reads
    `API-equivalent savings` (the figure is the API-equivalent
    value caching adds — the user pays the fixed monthly fee
    regardless)."""
    if plan.is_flat_rate:
        return "API-equivalent savings"
    return "Savings"


def render(state: SidebarState, nav: Navigation) -> None:
    st.markdown("# Cache")
    st.caption(_page_caption(state.plan))

    daily_report = load_daily(state)
    if daily_report is None:
        return

    _render_data_range_banner(daily_report, since_date=state.query.since_date())

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date "
            "range** in the sidebar, or clearing the **Project** filter "
            "if one is set."
        )
        return

    savings = cache_savings(daily_report)
    _render_savings_hero(savings, plan=state.plan)
    _render_kpi_row(daily_report, savings=savings, plan=state.plan)
    _render_reads_vs_writes(daily_report)
    _render_daily_savings(daily_report, plan=state.plan)
    _render_per_model_performance(daily_report, plan=state.plan)


# --- data-range banner --------------------------------------------------


def _render_data_range_banner(
    daily_report: DailyReport, *, since_date: date | None
) -> None:
    """If the actual cache data range starts AFTER the sidebar's
    selected `since`, surface that gap explicitly.

    The chart's X-axis can only show dates that exist in the data
    — if the user picked Apr 18 → May 17 but cache data only
    starts May 12, the charts read as "5 days of data" against a
    sidebar showing "30 days". Without context, the user is left
    to guess whether caching kicked in part-way through the
    window or there's a bug.

    `since_date` is the already-parsed sidebar `since` (caller
    invokes `state.query.since_date()`). No banner when the bound
    is missing or unparseable, or when the data covers / exceeds
    that start date.
    """
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


def _render_savings_hero(savings: dict | None, *, plan: Plan) -> None:
    """Large `$ saved` headline + plan-aware framing.

    The hero is the page's reason for existing — caching's value
    proposition stated in money. Plan-aware: on Enterprise the
    dollar IS real money saved; on flat-rate it's the API-equivalent
    value caching adds to throughput (the user pays the fixed
    monthly fee regardless).

    Label / context / no-rates fallback all come from module-private
    helpers (`_hero_label`, `_hero_savings_context_html`,
    `_hero_no_rates_context`) — single source of truth per plan
    branch.

    When pricing rates aren't resolvable (offline + no cache), the
    panel renders a neutral fallback that explains why the figure
    is missing rather than a placeholder $0.
    """
    label = _hero_label(plan)
    if savings is None:
        context = _hero_no_rates_context(plan)
        st.markdown(
            f"""
            <div class="tokenscope-cache-hero">
              <div class="tokenscope-cache-hero-label">
                {label}
              </div>
              <div class="tokenscope-cache-hero-value">—</div>
              <div class="tokenscope-cache-hero-context">
                {context}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    savings_usd = savings["savings_usd"]
    actual = savings["actual_cost_usd"]
    uncached = savings["uncached_cost_usd"]
    context = _hero_savings_context_html(
        plan,
        savings_usd=savings_usd,
        actual=actual,
        uncached=uncached,
    )
    st.markdown(
        f"""
        <div class="tokenscope-cache-hero">
          <div class="tokenscope-cache-hero-label">
            {label}
          </div>
          <div class="tokenscope-cache-hero-value">${savings_usd:,.2f}</div>
          <div class="tokenscope-cache-hero-context">
            {context}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- KPI row -----------------------------------------------------------


def _render_kpi_row(
    daily_report: DailyReport, *, savings: dict | None, plan: Plan
) -> None:
    """Supporting four-card KPI row. Hit ratio card embeds the
    sparkline; the other three are standard metric cards.

    `?` help icons are dropped where the label is self-evident (hit
    ratio, cache reads, cache writes); only the `Effective $/1M`
    card keeps one because the comparison-to-published-rate framing
    isn't obvious from the label alone.

    The Effective-$/MTok card is plan-aware — label / caption / help
    all come from module-private helpers (`_effective_rate_label`,
    `_effective_rate_caption`, `_effective_rate_help`). The other
    three cards (hit ratio, reads, writes) are plan-independent
    facts.
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
            _effective_rate_label(plan),
            f"${effective:,.3f}" if effective is not None else "—",
            help=_effective_rate_help(plan),
        )
        st.caption(_effective_rate_caption(plan))

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


def _render_daily_savings(daily_report: DailyReport, *, plan: Plan) -> None:
    """Per-day savings bar chart. Section header and body caption
    are plan-aware (see `_daily_savings_title` /
    `_daily_savings_caption`); the no-rates fallback is plan-
    independent — it's about LiteLLM reachability, not billing
    semantics — so both plans see the same `Savings unavailable`
    notice."""
    with st.container(border=True):
        st.markdown(_daily_savings_title(plan))
        st.caption(_daily_savings_caption(plan))
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


def _render_per_model_performance(
    daily_report: DailyReport, *, plan: Plan
) -> None:
    """Per-model cache performance. Conditional on >1 model in the
    window — a single-model window has nothing to compare, so the
    section is hidden entirely (no empty table, no single-row chart).

    Renders a horizontal stacked bar (cache_create + cache_read per
    model) PLUS a compact table with the hit ratio, savings, and
    raw token counts the bar doesn't surface.

    Section caption and the savings-column header are plan-aware
    (see `_per_model_caption` / `_per_model_savings_column_header`).
    The other columns (Cache hit ratio, Reads, Writes) are
    plan-independent facts.
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

    savings_col = _per_model_savings_column_header(plan)

    with st.container(border=True):
        st.markdown("### Per-model cache performance")
        st.caption(_per_model_caption(plan))
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
                savings_col: (
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
