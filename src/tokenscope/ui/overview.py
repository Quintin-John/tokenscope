"""Overview page.

The landing surface: page H1 + window-context subtitle, KPI strip in
cards, dynamic insight paragraph, inline cost composition, and charts
(cost trend with rolling overlay + percent-stacked token mix + a
non-cache mini chart that exposes the input/output/cache_create
variance that cache_read otherwise crushes).

Per PLAN.md §3.3 the cost-trend chart is the drill entry-point —
clicking a day sets `?view=day&day=YYYY-MM-DD` and reruns. The
underlying model filter from the sidebar is applied post-fetch via
`filter_daily_by_models`.

The Window-cost KPI re-frames itself based on the active plan
(`state.plan.is_flat_rate`). Enterprise (pay-per-token) shows ccusage's
API cost as the headline. Pro / Max-5× / Max-20× show the prorated
plan fee as the headline with the API-equivalent figure as a
"would-have-been" delta. The plan default lives in
`config.DEFAULT_PLAN_NAME`.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from tokenscope import config, data
from tokenscope.analytics import (
    aggregate_cache_hit_ratio,
    available_models,
    bold_numbers_in_insight,
    collapse_composition_rows,
    cost_by_kind,
    filter_daily_by_models,
    format_compact_int,
    format_timezone_for_display,
    last_day_cost,
    overview_insight,
    prior_window_query,
    spike_day,
    window_cost,
)
from tokenscope.ccusage import CcusageError
from tokenscope.models import DailyReport
from tokenscope.navigation import Navigation
from tokenscope.plans import Plan
from tokenscope.query import Query
from tokenscope.ui._data import load_daily
from tokenscope.ui._nav import handle_chart_drill
from tokenscope.ui.charts import (
    cost_trend_with_rolling,
    token_mix_non_cache_percent_bar,
    token_mix_percent_bar,
)
from tokenscope.ui.sidebar import SidebarState


# Composition-table delta vs source is suppressed below this threshold
# — the difference is rounding noise, not a meaningful divergence, and
# surfacing "0.0%" is just noise.
_COMPOSITION_DELTA_DISPLAY_THRESHOLD = 0.01

# Composition rows whose share of total cost is below this threshold
# get collapsed into a single "other" row. Token kinds whose
# contribution rounds to ≪ 0.01% are noise on a 4-row table; grouping
# them keeps the share-bar column legible without dropping the data.
_COMPOSITION_OTHER_THRESHOLD = 0.001  # 0.1%


def render(state: SidebarState, nav: Navigation, today: date | None = None) -> None:
    today = today or date.today()
    refresh_time = datetime.now()

    daily_report = load_daily(state)
    if daily_report is None:
        return

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

    window_days = _window_days(state.query) or config.DEFAULT_RANGE_DAYS
    spike = spike_day(daily_report, threshold_multiplier=config.OVERVIEW_SPIKE_THRESHOLD)

    _render_page_header(state, window_days, refresh_time)
    _render_kpis(daily_report, state.plan, state.query, prior_total, window_days)
    _render_insight(daily_report, prior_total, window_days, spike)

    # Enterprise users pay per token, so the per-kind composition is
    # actionable for them. Flat-rate plans pay a fixed monthly fee
    # regardless of mix, so this would be noise on those plans.
    if not state.plan.is_flat_rate and daily_report.daily:
        _render_cost_composition(daily_report)

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date range** "
            "in the sidebar, or clearing the **Project** filter if one is set."
        )
        return

    _render_cost_trend(daily_report, nav, spike)
    _render_token_mix(daily_report, nav)


# --- header --------------------------------------------------------------


def _render_page_header(
    state: SidebarState, window_days: int, refresh_time: datetime
) -> None:
    """Page H1 (`Overview`) + window/timezone subtitle on the left,
    `Updated HH:MM:SS` caption on the right. The product wordmark
    sits in the browser tab via `st.set_page_config`; the visible H1
    on every page should be the *view name*, not the product name.
    """
    cols = st.columns([4, 1])
    with cols[0]:
        st.markdown("# Overview")
        tz_display = format_timezone_for_display(state.query.tz or "")
        st.caption(
            f"Window: last {window_days} days · times in {tz_display}"
        )
    with cols[1]:
        # Right-align via markdown alignment — `st.caption` doesn't
        # support `text-align`. The trailing div+css class lives in
        # `_app_styles.css`.
        st.markdown(
            f'<div class="tokenscope-page-refresh">Updated '
            f'{refresh_time.strftime("%H:%M:%S")}</div>',
            unsafe_allow_html=True,
        )


# --- KPIs ---------------------------------------------------------------


def _render_kpis(
    daily_report: DailyReport,
    plan: Plan,
    query: Query,
    prior_total: float | None,
    window_days: int,
) -> None:
    """Four-card KPI strip. Each KPI lives inside a bordered container
    so the row reads as four grouped surfaces.

    Captions are plain English (`over 30 days`), not formulas
    (`window cost ÷ 30 days`) — the formula form leaked implementation
    detail into the user-facing UI. Help icons are reserved for the
    one card whose semantics genuinely don't self-explain (Cache hit
    ratio's denominator excludes output tokens, which surprises users
    who expect "share of all tokens").

    Window-cost delta uses `delta_color="off"` — increased cost is not
    good news in a cost dashboard, and the green-up arrow in Streamlit's
    `normal` mode was actively misleading. The previous `inverse` mode
    painted positive deltas red, which compounds the "red means warning"
    convention with "red means cost rose" — also misleading. Neutral
    gray for cost deltas is the only honest treatment.
    """
    api_window_cost = window_cost(daily_report)
    last_day = last_day_cost(daily_report)
    cache_ratio = aggregate_cache_hit_ratio(daily_report)
    avg_daily = api_window_cost / window_days if window_days > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)

    with c1, st.container(border=True):
        _render_window_cost_kpi(plan, api_window_cost, prior_total, window_days)

    with c2, st.container(border=True):
        if last_day is not None:
            st.metric("Last day", f"${last_day[1]:,.2f}")
            st.caption(f"on {last_day[0]}")
        else:
            st.metric("Last day", "—")
            st.caption("no data in window")

    with c3, st.container(border=True):
        st.metric("Avg daily cost", f"${avg_daily:,.2f}")
        st.caption(f"over {window_days} days")

    with c4, st.container(border=True):
        st.metric(
            "Cache hit ratio",
            f"{cache_ratio:.1%}",
            help=(
                "Share of input-side tokens served from cache. "
                "Excludes output tokens (the model generates those — "
                "they're not part of the cache decision)."
            ),
        )
        st.caption("share of input-side tokens served from cache")


def _render_window_cost_kpi(
    plan: Plan,
    api_window_cost: float,
    prior_total: float | None,
    window_days: int,
) -> None:
    """Window-cost KPI — flips between pay-per-token (Enterprise) and
    flat-rate (Pro / Max). Extracted so the four-card layout in
    `_render_kpis` reads as a parallel column comp.

    Cost delta uses `delta_color="off"` (neutral gray) — see
    `_render_kpis` docstring for why neither `normal` (positive=green)
    nor `inverse` (positive=red) is right for cost deltas.
    """
    if plan.is_flat_rate:
        savings = api_window_cost - plan.flat_rate_usd_per_month
        st.metric(
            f"Plan cost ({plan.name})",
            f"${plan.flat_rate_usd_per_month:,.0f}/mo",
            delta=f"would cost ${api_window_cost:,.2f} at API rates",
            delta_color="off",
        )
        st.caption(
            f"${abs(savings):,.2f} {'saved' if savings >= 0 else 'over'} "
            f"vs API rates this window"
        )
        return

    delta_kwargs: dict = {}
    if prior_total and prior_total > 0:
        change = (api_window_cost - prior_total) / prior_total
        delta_kwargs["delta"] = f"{change:+.0%} vs prior {window_days}d"
        # Cost dashboard semantics: spending more is bad news.
        # Streamlit's `inverse` mode paints positive deltas red with an
        # up arrow (cost up = warning) and negative deltas green with a
        # down arrow (cost down = good). That matches user expectation
        # for cost metrics specifically; the default `normal` mode
        # would paint "+91% more spend" in green, which is misleading.
        delta_kwargs["delta_color"] = "inverse"
    st.metric("Window cost", f"${api_window_cost:,.2f}", **delta_kwargs)
    st.caption(f"over the last {window_days} days")


# --- insight summary -----------------------------------------------------


def _render_insight(
    daily_report: DailyReport,
    prior_total: float | None,
    window_days: int,
    spike: tuple[str, float] | None,
) -> None:
    """One-paragraph dynamic summary of what the window contains.

    Numbers (dollar amounts, percentages) get wrapped in `<strong>`
    via `bold_numbers_in_insight` so the eye lands on the figures
    rather than the prose. Renders inside a left-bordered callout
    panel (CSS-driven) so it reads as narrative, distinct from the
    KPI cards.
    """
    paragraph = overview_insight(
        window_total_cost=window_cost(daily_report),
        window_days=window_days,
        prior_total=prior_total,
        spike=spike,
        cache_hit_ratio=aggregate_cache_hit_ratio(daily_report),
    )
    bolded = bold_numbers_in_insight(paragraph)
    st.markdown(
        f'<div class="tokenscope-insight">{bolded}</div>',
        unsafe_allow_html=True,
    )


# --- cost composition ----------------------------------------------------


def _render_cost_composition(daily_report: DailyReport) -> None:
    """Inline cost composition — section header + one-line subtitle +
    breakdown table + total row.

    The table collapses sub-0.1%-share kinds into a single "other"
    row so the share-bar column stays legible (a 0.004% slice
    rendered as a zero-pixel bar adds no information). Token counts
    use `format_compact_int` (4.9M / 1.6B). The estimate-vs-source
    delta only renders when |diff| ≥ 1% — zero noise.
    """
    rows = cost_by_kind(daily_report)
    if not rows or all(r["tokens"] == 0 for r in rows):
        return

    est_total = sum(r["est_cost"] for r in rows)
    actual_total = window_cost(daily_report)
    diff_pct = (
        (est_total - actual_total) / actual_total if actual_total else 0.0
    )
    total_tokens = sum(r["tokens"] for r in rows)
    collapsed = collapse_composition_rows(
        rows, hide_threshold=_COMPOSITION_OTHER_THRESHOLD
    )

    st.markdown("### Cost composition")
    st.caption(
        "Estimate of where the window's spend went, by token kind."
    )

    table_rows = [
        {
            "Kind": r["kind"],
            "Tokens": format_compact_int(r["tokens"]),
            "Est. cost (USD)": r["est_cost"],
            "Share": r["share"] * 100,
        }
        for r in collapsed
    ]
    table_rows.append(
        {
            "Kind": "total",
            "Tokens": format_compact_int(total_tokens),
            "Est. cost (USD)": est_total,
            "Share": 100.0,
        }
    )

    df = pd.DataFrame(table_rows)
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "Kind": st.column_config.TextColumn(width="small"),
            "Tokens": st.column_config.TextColumn(width="small"),
            "Est. cost (USD)": st.column_config.NumberColumn(
                format="$%.2f", width="medium"
            ),
            "Share": st.column_config.ProgressColumn(
                min_value=0.0, max_value=100.0, format="%.1f%%", width="medium"
            ),
        },
    )
    if abs(diff_pct) >= _COMPOSITION_DELTA_DISPLAY_THRESHOLD:
        st.caption(f"Estimate accuracy: {diff_pct:+.1%} vs source.")


# --- charts --------------------------------------------------------------


_COST_TREND_MODE_KEY = "overview-cost-trend-mode"


def _render_cost_trend(
    daily_report: DailyReport,
    nav: Navigation,
    spike: tuple[str, float] | None,
) -> None:
    """Cost-by-family chart with a Stack/Overlay toggle.

    - **Stacked** (default): family bands sum to total cost height.
      The conventional "where the money went" reading.
    - **Overlay**: each family rendered as its own non-stacked area
      with transparent fill, smallest-cost drawn last (so it sits
      ON TOP of dominant families). Surfaces small-usage families
      that the stacked view crushes against the baseline.

    Both modes carry the dotted 7-day rolling-average line and the
    spike annotation. Clicking a point still drills into the day view.
    """
    with st.container(border=True):
        title_cols = st.columns([3, 2])
        with title_cols[0]:
            st.markdown("### Daily cost")
        with title_cols[1]:
            mode_choice = st.segmented_control(
                "View",
                options=["Stacked", "Overlay"],
                default="Stacked",
                key=_COST_TREND_MODE_KEY,
                label_visibility="collapsed",
                help=(
                    "Stacked: family bands sum to total cost. "
                    "Overlay: each family plotted independently — "
                    "small-usage families stay visible even when one "
                    "family dominates."
                ),
            )
        mode = "overlay" if mode_choice == "Overlay" else "stacked"
        st.caption(
            "Dotted line is the 7-day rolling average. "
            "Click any day to drill in."
        )
        fig = cost_trend_with_rolling(
            daily_report,
            rolling_window_days=7,
            spike=spike,
            mode=mode,
        )
        if fig is None:
            return
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-cost-trend",
            on_select="rerun",
            selection_mode=("points",),
        )
        handle_chart_drill(
            event, lambda x: nav.to_day(x[:10]), chart_key="overview-cost-trend"
        )


_NON_CACHE_TOGGLE_KEY = "overview-token-mix-include-cache-read"


def _render_token_mix(daily_report: DailyReport, nav: Navigation) -> None:
    """Token-mix chart. Default renders the full percent-stacked bar
    across all four kinds. A toggle below switches to the non-cache
    re-base (input / output / cache_create only) so the variance
    cache_read otherwise crushes becomes legible.

    Both variants live in their own bordered card so the chart reads
    as a peer of the KPI strip rather than as bare Plotly output.
    """
    with st.container(border=True):
        st.markdown("### Token mix")
        st.caption(
            "What fraction of each day's tokens went to input / output / "
            "cache_create / cache_read. Each bar sums to 100%; absolute "
            "token counts surface on hover."
        )
        include_cache_read = st.toggle(
            "Include cache_read",
            value=True,
            key=_NON_CACHE_TOGGLE_KEY,
            help=(
                "Cache reads typically dominate every bar (~99% of "
                "tokens on Claude Code workloads), making the other "
                "three kinds visually flat. Turn this off to re-base "
                "on input / output / cache_create only — useful when "
                "you want to see the non-cache variance."
            ),
        )
        fig = (
            token_mix_percent_bar(daily_report)
            if include_cache_read
            else token_mix_non_cache_percent_bar(daily_report)
        )
        if fig is None:
            return
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="overview-token-mix",
            on_select="rerun",
            selection_mode=("points",),
        )
        handle_chart_drill(
            event, lambda x: nav.to_day(x[:10]), chart_key="overview-token-mix"
        )


# --- helpers -------------------------------------------------------------


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
