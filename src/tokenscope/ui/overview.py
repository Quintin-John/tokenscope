"""Overview page: KPIs + three charts. Click-to-drill on the stacked area.

Per PLAN.md §3.3 the stacked-area chart is the entry point to the day
view — clicking a day in the chart sets `?view=day&day=YYYY-MM-DD` and
reruns. The model filter from the sidebar is applied post-fetch via
`filter_daily_by_models`.

Slice 12 adds context to the Window cost KPI:
- On Enterprise (pay-as-you-go), the value is ccusage's API cost — your
  actual bill — and the delta compares to the prior equivalent window.
- On flat-rate plans (Pro / Max), the value is your prorated plan fee
  (what you actually pay) and the delta shows what the same usage would
  have cost at API rates ("would-have-been"). The previous
  "API-equivalent" banner is dropped because the KPI now self-explains.
"""

from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    active_block_burn,
    aggregate_cache_hit_ratio,
    available_models,
    cost_by_kind,
    filter_daily_by_models,
    last_day_cost,
    prior_window_query,
    window_cost,
)
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.plans import Plan
from tokenscope.query import Query
from tokenscope.ui._data import load_daily
from tokenscope.ui._nav import handle_chart_drill
from tokenscope.ui.charts import (
    rolling_average_line,
    stacked_area_cost_by_family,
    token_mix_bar,
)
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation, today: date | None = None) -> None:
    today = today or date.today()

    daily_report = load_daily(state)
    if daily_report is None:
        return

    try:
        blocks_report = data.blocks(active=True, query=state.query)
    except CcusageError:
        blocks_report = None

    # Prior-period comparison fetch (cached). Only meaningful when the
    # current query has explicit since/until.
    prior_total: float | None = None
    prior_q = prior_window_query(state.query)
    chosen = set(state.selected_models)
    if prior_q is not None:
        try:
            prior_report = data.daily(prior_q)
            if chosen and chosen != set(available_models(prior_report)):
                prior_report = filter_daily_by_models(prior_report, chosen)
            prior_total = window_cost(prior_report)
        except CcusageError:
            prior_total = None

    _render_kpis(daily_report, blocks_report, state.plan, state.query, prior_total)

    # Enterprise users pay per token, so the per-kind composition is
    # actionable for them. Flat-rate plans (Pro / Max) pay a fixed
    # monthly fee regardless of mix, so this view would be noise.
    if not state.plan.is_flat_rate and daily_report.daily:
        _render_cost_composition(daily_report)

    st.divider()

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date range** "
            "in the sidebar, or clearing the **Project** filter if one is set."
        )
        return

    st.subheader("Daily cost by model family")
    fig = stacked_area_cost_by_family(daily_report)
    if fig is not None:
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-stacked-area",
            on_select="rerun",
            selection_mode=("points",),
        )
        handle_chart_drill(event, lambda x: nav.to_day(x[:10]))

    st.subheader("7-day rolling average cost")
    fig = rolling_average_line(daily_report, window_days=7)
    if fig is not None:
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-rolling-line",
            on_select="rerun",
            selection_mode=("points",),
        )
        handle_chart_drill(event, lambda x: nav.to_day(x[:10]))

    st.subheader("Daily token mix")
    fig = token_mix_bar(daily_report)
    if fig is not None:
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-token-mix",
            on_select="rerun",
            selection_mode=("points",),
        )
        handle_chart_drill(event, lambda x: nav.to_day(x[:10]))


def _render_kpis(
    daily_report,
    blocks_report,
    plan: Plan,
    query: Query,
    prior_total: float | None,
) -> None:
    api_window_cost = window_cost(daily_report)
    last_day = last_day_cost(daily_report)
    burn = active_block_burn(blocks_report) if blocks_report is not None else None
    cache_ratio = aggregate_cache_hit_ratio(daily_report)

    c1, c2, c3, c4 = st.columns(4)

    if plan.is_flat_rate:
        # Flat-rate plans (Pro / Max): the user pays their monthly fee
        # *regardless of window length*. Showing a prorated number was
        # confusing — implied the price varies with date-picker selection
        # when it doesn't. Headline is the flat monthly fee verbatim;
        # the API-equivalent is the savings-context delta.
        savings = api_window_cost - plan.flat_rate_usd_per_month
        c1.metric(
            f"Plan cost ({plan.name})",
            f"${plan.flat_rate_usd_per_month:,.0f}/mo",
            delta=(
                f"would cost ${api_window_cost:,.2f} at API rates this window"
            ),
            delta_color="off",
            help=(
                f"Your {plan.name} plan is flat-rate at "
                f"${plan.flat_rate_usd_per_month:.0f}/month — paid regardless of "
                f"how many tokens you push or how long this window is. "
                f"The delta is what the same usage would have cost at API rates "
                f"(${abs(savings):,.2f} {'saved' if savings >= 0 else 'over'} "
                f"vs your subscription)."
            ),
        )
    else:
        # Enterprise / pay-as-you-go: ccusage's API cost is the actual bill.
        # Delta compares to the prior equivalent window.
        delta_kwargs: dict = {}
        if prior_total and prior_total > 0:
            change = (api_window_cost - prior_total) / prior_total
            delta_kwargs["delta"] = f"{change:+.0%} vs prior {_window_days(query) or 30}d"
        c1.metric(
            "Window cost",
            f"${api_window_cost:,.2f}",
            help="Sum of total_cost across every day in the selected date range. "
                 "Delta compares to the previous equivalent-length window.",
            **delta_kwargs,
        )

    if last_day is not None:
        c2.metric(
            f"Last day ({last_day[0]})",
            f"${last_day[1]:,.2f}",
            help="Cost on the most recent day with data inside the window.",
        )
    else:
        c2.metric("Last day", "—")
    c3.metric(
        "Active block $/hr",
        f"${burn:,.2f}" if burn is not None else "—",
        help="Cost-per-hour for the currently-active 5-hour billing window.",
    )
    c4.metric(
        "Cache hit ratio",
        f"{cache_ratio:.1%}",
        help="cache_read / (input + cache_create + cache_read), aggregated.",
    )


def _render_cost_composition(daily_report) -> None:
    """Enterprise-only: break window cost into estimated $-per-kind so
    the user sees where the money is actually going (mostly cache_read
    for typical Claude Code use).

    The numbers are an estimate — ccusage doesn't break out per-kind
    cost, so we multiply token counts by Anthropic's published rate
    schedule (`tokenscope.pricing.RATES`). The estimated total
    typically lands within a few percent of ccusage's reported total;
    we show both so the gap is honest.
    """
    import pandas as pd

    rows = cost_by_kind(daily_report)
    if not rows or all(r["tokens"] == 0 for r in rows):
        return

    est_total = sum(r["est_cost"] for r in rows)
    actual_total = window_cost(daily_report)
    diff_pct = ((est_total - actual_total) / actual_total) if actual_total else 0.0

    with st.expander(
        f"Cost composition — where the ${actual_total:,.2f} went "
        f"(est. ${est_total:,.2f}, {diff_pct:+.1%} vs ccusage)",
        expanded=False,
    ):
        st.caption(
            "Tokens × Anthropic's per-kind rate. Cache reads cost a small "
            "fraction of input rate, output tokens cost several times input "
            "— that's why the mix matters more than the raw token total. "
            "The estimate can differ from ccusage's reported total when "
            "promotional discounts or model-version pricing nuance apply."
        )
        # ProgressColumn's `format` string is applied to the raw value with no
        # implicit ×100 — so a 0–1 fraction with format "%.1f%%" renders as
        # "0.6%" instead of "65.0%". Pre-multiply and widen min/max to 100.
        df = pd.DataFrame(
            [
                {
                    "Kind": r["kind"],
                    "Tokens": r["tokens"],
                    "Est. cost (USD)": r["est_cost"],
                    "Share": r["share"] * 100,
                }
                for r in rows
            ]
        )
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={
                "Tokens": st.column_config.NumberColumn(format="%d"),
                "Est. cost (USD)": st.column_config.NumberColumn(format="$%.2f"),
                "Share": st.column_config.ProgressColumn(
                    min_value=0.0, max_value=100.0, format="%.1f%%"
                ),
            },
        )


def _window_days(query: Query) -> int | None:
    """Length of the current window in days, or None when unbounded."""
    if not query.since or not query.until:
        return None
    try:
        since = datetime.strptime(query.since, "%Y%m%d").date()
        until = datetime.strptime(query.until, "%Y%m%d").date()
    except ValueError:
        return None
    return (until - since).days + 1


