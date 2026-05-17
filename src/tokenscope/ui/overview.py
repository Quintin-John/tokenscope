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
    filter_daily_by_models,
    last_day_cost,
    prior_window_query,
    window_cost,
)
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.plans import Plan
from tokenscope.query import Query
from tokenscope.ui.charts import (
    rolling_average_line,
    stacked_area_cost_by_family,
    token_mix_bar,
)
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation, today: date | None = None) -> None:
    today = today or date.today()

    try:
        daily_report = data.daily(state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    # Apply post-fetch model filter — only when user has narrowed below "all".
    all_models = available_models(daily_report)
    chosen = set(state.selected_models)
    if chosen and chosen != set(all_models):
        daily_report = filter_daily_by_models(daily_report, chosen)

    try:
        blocks_report = data.blocks(active=True, query=state.query)
    except CcusageError:
        blocks_report = None

    # Prior-period comparison fetch (cached). Only meaningful when the
    # current query has explicit since/until.
    prior_total: float | None = None
    prior_q = prior_window_query(state.query)
    if prior_q is not None:
        try:
            prior_report = data.daily(prior_q)
            if chosen and chosen != set(available_models(prior_report)):
                prior_report = filter_daily_by_models(prior_report, chosen)
            prior_total = window_cost(prior_report)
        except CcusageError:
            prior_total = None

    _render_kpis(daily_report, blocks_report, state.plan, state.query, prior_total)
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
        _handle_day_click(event, nav)

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
        _handle_day_click(event, nav)

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
        _handle_day_click(event, nav)


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
        # regardless of token volume. Headline is the prorated plan cost
        # for this window; the API-equivalent appears as a "would have
        # cost" delta so the user knows what they're saving.
        days = _window_days(query) or 30
        plan_cost = plan.flat_rate_usd_per_month * (days / 30.0)
        savings = api_window_cost - plan_cost
        c1.metric(
            f"Window cost ({plan.name})",
            f"${plan_cost:,.2f}",
            delta=f"would cost ${api_window_cost:,.2f} at API rates",
            delta_color="off",
            help=(
                f"Your {plan.name} plan is flat-rate at "
                f"${plan.flat_rate_usd_per_month:.0f}/month — this is the prorated "
                f"cost for the selected window ({days}d). The delta is what the "
                f"same usage would have cost at API rates "
                f"(${savings:,.2f} {'saved' if savings >= 0 else 'over'})."
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


def _handle_day_click(event, nav: Navigation) -> None:
    """If the user clicked a point in an overview chart, drill into that day."""
    if not event:
        return
    selection = getattr(event, "selection", None)
    if not selection:
        return
    points = getattr(selection, "points", None) or []
    if not points:
        return
    raw = points[0].get("x")
    if not raw:
        return
    day = str(raw)[:10]  # YYYY-MM-DD prefix
    target = nav.to_day(day)
    st.query_params.clear()
    for k, v in target.to_params().items():
        st.query_params[k] = v
    st.rerun()
