"""Active-block live view.

A real-time snapshot of the user's current ccusage activity window.
The window's billing meaning is plan-aware: on flat-rate plans (Pro
/ Max) it IS the user's quota reset window; on Enterprise it's just
activity bucketing with no billing significance. Plan-aware copy
lives in module-private helpers (`_banner_label`,
`_banner_reset_suffix`, `_page_caption`, `_empty_state_text`) — see
their docstrings for the framing rules.

Replaces the prior gauge-only layout (which duplicated the `$/hr`
KPI in a larger, scaled-to-an-arbitrary-axis form and carried a
cryptic unlabelled delta) with:

  * `# Live` page header + a plan-aware one-line subtitle.
  * Window banner — plan-aware label + start → end time + plan-aware
    reset suffix + models active. Light-bg panel, distinct from KPI
    cards.
  * KPI strip — Cost so far / $/hr / Tokens/min / Projected total,
    all wrapped in `st.container(border=True)` cards matching the
    Overview look.
  * Spend trajectory chart — cumulative cost line from window start
    through "now" with a dashed projection continuation to window
    end. The chart's slope IS the burn rate; users see whether
    spend is accelerating or steady at a glance.
  * Projected-token caption — the one piece of the prior "Projection
    detail" expander that wasn't duplicated elsewhere.

Implementation notes:

- The refreshing panel is a `@st.fragment(run_every=...)`. Only the
  panel re-runs on the timer — the page selector, sidebar, and
  breadcrumbs are not affected.
- The live ccusage call bypasses `tokenscope.data` (which is wrapped
  in `@st.cache_data(ttl=30)`). Compounding two 30s windows would
  give the user a snapshot up to a minute stale, which defeats
  "live". The fragment is the only refresh cadence.
- The spend-trajectory chart is a two-point actual line (block start
  → now) plus a dashed projection to the window end. Each fragment
  refresh rebuilds it from the latest ccusage snapshot; no
  intra-block sample history is persisted.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from tokenscope import ccusage, config
from tokenscope.analytics import (
    UNKNOWN_MODEL_FAMILY,
    block_cache_hit_ratio,
    block_cost_by_kind,
    block_token_counts_by_kind,
    format_compact_int,
    format_timezone_for_display,
    typical_burn_rate,
)
from tokenscope.ccusage import CcusageError
from tokenscope.log import get_logger
from tokenscope.models import BlockEntry
from tokenscope.navigation import Navigation
from tokenscope.plans import Plan
from tokenscope.pricing import KINDS
from tokenscope.query import Query
from tokenscope.ui.charts import (
    PALETTE,
    live_spend_trajectory,
    live_token_kind_composition_bar,
)
from tokenscope.ui.sidebar import SidebarState

_log = get_logger(__name__)

# Display labels for the four token kinds. Keyed by `pricing.KINDS`
# (the canonical kind tuple) so the swatch lookup is the same string
# used to colour every other chart trace named for that kind. Iteration
# order across the Live KPI cards and mini-table comes from `KINDS`
# directly — no parallel order tuple to drift.
_TOKEN_KIND_LABELS: dict[str, str] = {
    "input": "Input",
    "output": "Output",
    "cache_create": "Cache create",
    "cache_read": "Cache read",
}

REFRESH_SECONDS = config.LIVE_REFRESH_SECONDS


# --- plan-aware copy (single source of truth) ---------------------------
#
# Every plan-aware string the Live view renders lives here, keyed off
# `plan.is_flat_rate`. The render functions never embed plan-keyed
# literals inline — they call these helpers. A copy edit (e.g. wording
# polish, translation) happens in ONE place; the branching logic
# (flat-rate vs pay-per-token) is defined once and consumed everywhere.
#
# Why module-private here and not on the `Plan` class: this is
# Live-view-specific UI text, not domain semantics. Putting it on
# `Plan` would tie the domain model to one view's vocabulary; keeping
# it module-private to live.py respects SRP — `Plan` knows about
# subscription pricing, `live.py` knows about its own UI.


def _banner_label(plan: Plan) -> str:
    """The first word of the window-banner line — "Quota window" on
    flat-rate (the 5h block IS the user's quota reset window),
    "Current activity" on Enterprise (no quota; the block is just
    ccusage's activity bucketing)."""
    return "Quota window" if plan.is_flat_rate else "Current activity"


def _banner_reset_suffix(plan: Plan, remaining_minutes: int | None) -> str:
    """The suffix appended after the time range. Reset countdown on
    flat-rate, explicit "unknown" sentinel when the countdown is
    missing on flat-rate, empty on Enterprise.

    No-guessing contract: when `remaining_minutes is None` on
    flat-rate, we say "reset time unknown" rather than silently
    dropping the suffix (which would visually imply no reset) or
    fabricating a number from the hardcoded 5-hour assumption."""
    if not plan.is_flat_rate:
        return ""
    if remaining_minutes is None:
        return " · reset time unknown"
    return f" · resets in {remaining_minutes} min"


def _page_caption(plan: Plan) -> str:
    """One-line subtitle under the `# Live` H1. Names the user's
    actual billing reality: flat-rate users see "your monthly fee
    is what you pay"; Enterprise sees "this is your actual API
    spend"."""
    if plan.is_flat_rate:
        return (
            "Real-time snapshot of your quota window. Costs are "
            "estimates at API rates; your actual bill is the plan's "
            "monthly fee."
        )
    return (
        "Real-time snapshot of your current Claude Code activity. "
        "Costs reflect actual API billing."
    )


def _empty_state_text(plan: Plan) -> str:
    """Info-banner text rendered when there is no active block.
    Plan-aware because the underlying concept differs — flat-rate
    users have a "quota window" to start; Enterprise users have a
    "session"."""
    if plan.is_flat_rate:
        return (
            "No active quota window right now. Start a Claude Code "
            "session to see live spend."
        )
    return (
        "No active Claude Code session right now. Start one to "
        "see live spend."
    )


def _window_noun(plan: Plan) -> str:
    """The plan-aware noun for the currently-active block:

      * "quota window" on flat-rate (the 5-hour block IS the user's
        quota reset window).
      * "session" on Enterprise (no quota; the block is just
        ccusage's activity bucketing).

    Every Live-view caption / card title / chart-fallback that
    refers to the block-in-context interpolates this single source
    of truth — a vocabulary change here propagates through every
    surface in one edit."""
    return "quota window" if plan.is_flat_rate else "session"


def _cost_so_far_caption(plan: Plan) -> str:
    """KPI caption for the Cost-so-far card. Names the user's
    billing reality alongside the window context: on flat-rate the
    dollar figure is an API-rate estimate (the actual bill is the
    monthly fee); on Enterprise it's the user's actual spend."""
    suffix = (
        "estimated at API rates" if plan.is_flat_rate else "actual cost"
    )
    return f"this {_window_noun(plan)} · {suffix}"


def _spend_chart_title(plan: Plan) -> str:
    """Section header for the spend-trajectory card."""
    return f"### Spend in this {_window_noun(plan)}"


def _spend_chart_caption(plan: Plan) -> str:
    """Body caption beneath the spend-trajectory chart. On flat-rate
    plans, names the decoupling between the API-equivalent chart
    values and the user's actual flat monthly fee — the chart's
    dollar projection is NOT what the user will pay."""
    if plan.is_flat_rate:
        return (
            f"Cumulative API-equivalent spend in your current "
            f"{_window_noun(plan)}. Your plan's monthly fee is fixed "
            "regardless of this projection."
        )
    return (
        f"Cumulative actual spend since the current "
        f"{_window_noun(plan)} started."
    )


def _spend_chart_no_projection_caption(plan: Plan) -> str:
    """Fallback caption when ccusage hasn't computed a projection for
    the active block yet (typically: brand-new block with no
    burn-rate data)."""
    return f"No projection available for this {_window_noun(plan)} yet."


def _token_mix_title(plan: Plan) -> str:
    """Section header for the token-mix composition card."""
    return f"### Token mix in this {_window_noun(plan)}"


def _token_mix_caption(plan: Plan) -> str:
    """Body caption beneath the token-mix composition bar."""
    return (
        f"Cumulative token mix for your current {_window_noun(plan)}. "
        f"Updated every {REFRESH_SECONDS}s."
    )


def _token_mix_empty_caption(plan: Plan) -> str:
    """Fallback caption when the active block has zero token activity
    across all four kinds."""
    return f"This {_window_noun(plan)} has no token activity yet."


def _projected_total_caption(plan: Plan) -> str:
    """Caption beneath the Projected-total KPI when projection data
    is present.

      * Flat-rate: names the API-equivalent semantics + flat-fee
        decoupling — the dollar projection is NOT what the user
        pays.
      * Enterprise: "at the current rate" — the dollar IS the
        user's incremental spend rate, so no qualifier needed.

    The no-projection fallback caption ("no projection") is
    plan-independent — it's a data-missing state, not a plan-aware
    semantic — and stays inline in `_render_projected_total_kpi`.
    """
    if plan.is_flat_rate:
        return (
            f"API-equivalent projection for this {_window_noun(plan)}; "
            "your plan's monthly fee is fixed."
        )
    return "at the current rate"


def render(state: SidebarState, nav: Navigation) -> None:
    """Live view shell: H1 + plan-aware subtitle, then the fragment-
    refreshed panel for everything else.

    The subtitle names the user's actual billing reality: on flat-rate
    plans the dollar figures on this view are API-equivalent estimates
    (the user pays the fixed monthly fee), while on Enterprise the
    figures are the user's actual API-billed spend. Same fix the
    Overview Window-cost KPI already applies — extending the
    plan-honest framing to the Live view.
    """
    st.markdown("# Live")
    st.caption(_page_caption(state.plan))
    _live_panel(plan=state.plan, offline=state.query.offline, tz=state.query.tz)


@st.fragment(run_every=REFRESH_SECONDS)
def _live_panel(plan: Plan, offline: bool, tz: str | None = None) -> None:
    """Auto-refreshing live panel. Args must be hashable so Streamlit
    can key the fragment; `Plan` is a frozen dataclass and hashable."""
    refreshed_at = datetime.now()
    now_utc = datetime.now(timezone.utc)
    try:
        report = ccusage.blocks(active=False, query=Query(offline=offline, tz=tz))
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    active = next((b for b in report.blocks if b.is_active), None)
    _render_refresh_line(refreshed_at)

    if active is None:
        st.info(_empty_state_text(plan))
        return

    typical = typical_burn_rate(report)
    _render_window_banner(active, plan=plan, tz=tz)
    _render_kpis(active, plan=plan, typical=typical)
    _render_token_kind_kpis(active)
    _render_cache_hit_callout(active)
    now_iso = _now_iso(now_utc)
    _log.info(
        "live.block_snapshot id=%s start=%s end=%s entries=%d "
        "total_tokens=%d cost_usd=%.2f",
        active.id,
        active.start_time,
        active.end_time,
        active.entries,
        active.total_tokens,
        active.cost_usd,
    )
    _render_spend_trajectory(active, plan=plan, now_iso=now_iso, tz=tz)
    _render_token_kind_composition(active, plan=plan)


# --- refresh indicator ---------------------------------------------------


def _render_refresh_line(refreshed_at: datetime) -> None:
    """Single-line `Last refreshed HH:MM:SS · auto-refreshes every Ns`
    with a small pulsing dot to telegraph that the panel is alive.

    The previous build had TWO separate caption lines saying nearly
    the same thing; this collapses both into one. The pulsing dot is
    a CSS animation that runs continuously — the user reads it as
    "the page IS live" even between refreshes.
    """
    st.markdown(
        f"""
        <div class="tokenscope-live-refresh">
          <span class="tokenscope-live-pulse"></span>
          Last refreshed {refreshed_at.strftime("%H:%M:%S")} ·
          auto-refreshes every {REFRESH_SECONDS}s
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- window banner -------------------------------------------------------


def _render_window_banner(
    active: BlockEntry, *, plan: Plan, tz: str | None
) -> None:
    """Two-line banner under the H1.

    Line 1: plan-aware label + time range + plan-aware reset suffix
    (see `_banner_label` and `_banner_reset_suffix` for the copy and
    the no-guessing semantics).

    Line 2: models active in the block. Plan-independent.

    Time range renders in the user's display timezone (sidebar's
    detected zone) with underscores stripped from the IANA
    identifier. If no tz is configured, falls back to UTC.
    """
    if tz:
        from tokenscope.tz import utc_iso_to_local_clock

        start_disp = (
            utc_iso_to_local_clock(active.start_time, tz) or active.start_time
        )
        end_disp = (
            utc_iso_to_local_clock(active.end_time, tz) or active.end_time
        )
        tz_label = format_timezone_for_display(tz)
    else:
        start_disp = active.start_time
        end_disp = active.end_time
        tz_label = "UTC"

    minutes_remaining = (
        active.projection.remaining_minutes if active.projection else None
    )
    label = _banner_label(plan)
    suffix = _banner_reset_suffix(plan, minutes_remaining)

    models = (
        ", ".join(active.models)
        if active.models
        else UNKNOWN_MODEL_FAMILY
    )

    st.markdown(
        f"""
        <div class="tokenscope-live-banner">
          <div class="tokenscope-live-banner-row">
            <strong>{label}</strong>
            · {start_disp} – {end_disp} {tz_label}{suffix}
          </div>
          <div class="tokenscope-live-banner-row tokenscope-live-banner-sub">
            Models in use: {models}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- KPIs ---------------------------------------------------------------


def _render_kpis(
    active: BlockEntry, *, plan: Plan, typical: float | None
) -> None:
    """Four-card KPI row matching the Overview look. Every card has a
    value + a one-line plain-English caption — no formula captions,
    no help icons (the labels are self-explanatory; cache_hit-style
    surprises don't apply on this view).

    `45 min left` moved OUT of the Projected-total card and into the
    window banner above — minutes remaining is a property of the
    window, not the projected cost.

    The Cost-so-far caption is plan-aware (see `_cost_so_far_caption`).
    The other three cards' captions are plan-independent (`vs typical`
    delta semantics, `indicator-weighted`, `at the current rate` all
    describe the metric mechanics, not the user's billing reality).
    """
    c1, c2, c3, c4 = st.columns(4)

    with c1, st.container(border=True):
        st.metric("Cost so far", f"${active.cost_usd:,.2f}")
        st.caption(_cost_so_far_caption(plan))

    with c2, st.container(border=True):
        if active.burn_rate is not None:
            kwargs: dict = {}
            if typical is not None and typical > 0:
                change = (active.burn_rate.cost_per_hour - typical) / typical
                kwargs["delta"] = f"{change:+.0%} vs typical"
                # Cost up = bad: inverse paints positive red, negative
                # green — same convention the Overview Window-cost
                # delta uses.
                kwargs["delta_color"] = "inverse"
            st.metric("$/hr", f"${active.burn_rate.cost_per_hour:,.2f}", **kwargs)
            st.caption(
                f"vs typical ${typical:,.2f}/hr"
                if typical is not None and typical > 0
                else "current rate"
            )
        else:
            st.metric("$/hr", "—")
            st.caption("no burn rate yet")

    with c3, st.container(border=True):
        if active.burn_rate is not None:
            st.metric(
                "Tokens / min",
                f"{active.burn_rate.tokens_per_minute:,.0f}",
            )
            st.caption("indicator-weighted")
        else:
            st.metric("Tokens / min", "—")
            st.caption("no burn rate yet")

    with c4, st.container(border=True):
        _render_projected_total_kpi(active, plan=plan)


# --- projected-total KPI (extracted, plan-aware) ------------------------


def _render_projected_total_kpi(active: BlockEntry, *, plan: Plan) -> None:
    """Plan-aware Projected-total KPI card.

    On flat-rate plans (Pro / Max 5× / Max 20×), the dollar
    projection misrepresents user exposure — the user pays the
    monthly flat fee, not the API-equivalent projection. Flip the
    metric headline to the plan fee and surface the API-equivalent
    figure as the delta. Architecturally identical to the Overview's
    `_render_window_cost_kpi` flat-rate branch
    (overview.py:285-296).

    On Enterprise / pay-per-token, the dollar projection IS the
    user's actual incremental spend; leave the headline unchanged.

    When `active.projection is None`, both plans render the same
    no-data state ("Projected total: —" + "no projection") — the
    missing data isn't plan-aware.
    """
    if active.projection is None:
        st.metric("Projected total", "—")
        st.caption("no projection")
        return

    api_projection = active.projection.total_cost
    if plan.is_flat_rate:
        st.metric(
            f"Plan cost ({plan.name})",
            f"${plan.flat_rate_usd_per_month:,.0f}/mo",
            delta=f"would cost ${api_projection:,.2f} at API rates",
            # Cost-comparison deltas use neutral gray — see Overview's
            # `_render_window_cost_kpi` docstring for the rationale
            # (neither green nor red is honest for a cost delta).
            delta_color="off",
        )
    else:
        st.metric("Projected total", f"${api_projection:,.2f}")
    st.caption(_projected_total_caption(plan))


# --- spend trajectory chart ---------------------------------------------


def _now_iso(now_utc: datetime) -> str:
    """ISO-8601 "now" string in the format the chart layer expects
    (`...Z` suffix, second precision)."""
    return now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def _render_spend_trajectory(
    active: BlockEntry,
    *,
    plan: Plan,
    now_iso: str,
    tz: str | None,
) -> None:
    """Cumulative-spend line with dashed projection to window end.

    Section header and body caption are plan-aware (see
    `_spend_chart_title` and `_spend_chart_caption`). The no-projection
    fallback caption is also plan-aware (see
    `_spend_chart_no_projection_caption`).

    Two anchor points: (block.start_time, $0) and (now, block.cost_usd),
    plus a dashed projection segment from "now" to the window end.
    The slope of the actual segment IS the average burn rate so far
    in the active window — users see acceleration vs. projection at
    a glance.

    The ``tz`` parameter routes the user's IANA zone through to the
    chart builder so every X-axis tick renders in local clock time
    rather than UTC.
    """
    with st.container(border=True):
        st.markdown(_spend_chart_title(plan))
        st.caption(_spend_chart_caption(plan))
        fig = live_spend_trajectory(active, now_iso=now_iso, tz=tz)
        if fig is None:
            st.caption(_spend_chart_no_projection_caption(plan))
            return
        st.plotly_chart(
            fig, width="stretch", key="live-spend-trajectory"
        )
        if active.projection is not None:
            st.caption(
                f"Projected total tokens: "
                f"{format_compact_int(active.projection.total_tokens)}"
            )


# --- token-kind KPIs ----------------------------------------------------


def _render_token_kind_kpis(active: BlockEntry) -> None:
    """Second KPI row — four cards, one per token kind.

    Each card carries:

      * The kind's PALETTE colour as a 12×12 swatch beside the
        label, so the visual category (input is pink, output is
        blue, ...) is established BEFORE the user reads the
        throughput chart below. The same swatch hue paints the
        matching band in `live_token_throughput`, so the cards
        and the chart share one mental mapping.
      * Abbreviated token count (`format_compact_int`) — the
        magnitudes span 5+ orders of magnitude (cache_read in
        the millions, input in the thousands), so full integers
        would dominate the card.
      * Estimated cost contribution, derived by `block_cost_by_kind`
        from LiteLLM pricing rates. The per-kind costs always sum
        to `block.cost_usd` (the actual cost ccusage reported);
        only the split between kinds is an approximation. Hidden
        as `—` when rates aren't resolvable (offline + no cache).
    """
    cost_rows = block_cost_by_kind(active)
    cost_by_kind = (
        {row["kind"]: row["est_cost"] for row in cost_rows}
        if cost_rows is not None
        else None
    )
    counts = block_token_counts_by_kind(active)

    cols = st.columns(len(KINDS))
    for col, kind in zip(cols, KINDS):
        label = _TOKEN_KIND_LABELS[kind]
        color = PALETTE[kind]
        token_count = counts[kind]
        est_cost = cost_by_kind.get(kind) if cost_by_kind is not None else None

        with col, st.container(border=True):
            st.markdown(
                f"""
                <div class="tokenscope-kind-card-header">
                  <span class="tokenscope-kind-swatch"
                        style="background:{color};"></span>
                  <span class="tokenscope-kind-card-label">{label}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='tokenscope-kind-card-value'>"
                f"{format_compact_int(token_count)} tokens"
                f"</div>",
                unsafe_allow_html=True,
            )
            if est_cost is not None:
                st.caption(f"≈ ${est_cost:,.2f} of block cost")
            else:
                st.caption("cost estimate unavailable")


def _render_cache_hit_callout(active: BlockEntry) -> None:
    """Cache hit ratio rendered as a composite / derived stat,
    visually distinct from the four token-kind KPI cards.

    Slate-tinted background + teal left border (same hue as
    `cache_read` in PALETTE — visual breadcrumb that this stat is
    derived from cache_read divided by cache-eligible total) so
    the eye reads it as "derived from the four kinds", not "a
    fifth raw count". The supporting copy is plain English
    ("share of input-side tokens served from cache") rather than
    the formula — same fix the Overview KPI already had.
    """
    ratio = block_cache_hit_ratio(active)
    pct = ratio * 100
    st.markdown(
        f"""
        <div class="tokenscope-cache-ratio-callout">
          <div class="tokenscope-cache-ratio-value">{pct:.1f}%</div>
          <div class="tokenscope-cache-ratio-label">
            Cache hit ratio · share of input-side tokens served from cache
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_token_kind_composition(active: BlockEntry, *, plan: Plan) -> None:
    """Horizontal stacked bar of the block's aggregate token-kind
    composition + a mini-table with absolute counts, estimated
    cost contribution, and share %.

    Section header / body caption / empty-state fallback are all
    plan-aware (see `_token_mix_title`, `_token_mix_caption`,
    `_token_mix_empty_caption`).

    Honest answer to the question "what kinds of tokens has this
    window burned?" given the data ccusage exposes (block-level
    aggregates only — no intra-block timestamps, no recoverable
    pre-page-load history). The prior `Token throughput` chart was
    structurally impossible from this data; this composition
    snapshot is.

    The mini-table mirrors the Overview Cost composition table's
    row shape (Kind · Tokens · Est. cost · Share %) so a user
    moving between Overview and Live reads the same vocabulary.
    """
    with st.container(border=True):
        st.markdown(_token_mix_title(plan))
        st.caption(_token_mix_caption(plan))
        fig = live_token_kind_composition_bar(active)
        if fig is None:
            st.caption(_token_mix_empty_caption(plan))
            return
        st.plotly_chart(
            fig, width="stretch", key="live-token-mix"
        )
        _render_token_kind_table(active)


def _render_token_kind_table(active: BlockEntry) -> None:
    """4-row mini-table beneath the composition bar. Same column
    set as the Overview Cost composition table so the vocabulary
    is consistent across the app.

    `Est. cost` rides the same `block_cost_by_kind` helper the
    token-kind KPI cards already use (rate-weighted split of
    `block.cost_usd`). When LiteLLM rates aren't resolvable
    (offline + no cache), the column renders `—` rather than
    fabricated zeros."""
    cost_rows = block_cost_by_kind(active)
    cost_by_kind = (
        {row["kind"]: row["est_cost"] for row in cost_rows}
        if cost_rows is not None
        else None
    )
    counts = block_token_counts_by_kind(active)
    total = sum(counts.values()) or 1  # avoid div-by-zero — caller short-circuited on 0
    rows = []
    for kind in KINDS:
        tokens = counts[kind]
        share = tokens / total * 100
        est_cost = cost_by_kind.get(kind) if cost_by_kind is not None else None
        rows.append(
            {
                "Kind": _TOKEN_KIND_LABELS[kind],
                "Tokens": format_compact_int(tokens),
                "Est. cost": (
                    f"${est_cost:,.2f}" if est_cost is not None else "—"
                ),
                "Share %": f"{share:.1f}%",
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
