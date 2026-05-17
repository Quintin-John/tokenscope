"""Active-block live view.

A real-time snapshot of the current 5-hour billing window. Replaces
the prior gauge-only layout (which duplicated the `$/hr` KPI in a
larger, scaled-to-an-arbitrary-axis form and carried a cryptic
unlabelled delta) with:

  * `# Live` page header + a one-line subtitle.
  * Window banner — start → end time, minutes remaining, models
    active. Light-bg panel, distinct from KPI cards.
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
- Spend-trajectory samples are appended to `st.session_state` on
  each fragment refresh so the chart's solid line gets richer as
  the user keeps the page open. Samples are keyed by block id so
  a new billing block starts the history fresh.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from tokenscope import ccusage, config
from tokenscope.analytics import (
    UNKNOWN_MODEL_FAMILY,
    format_compact_int,
    format_timezone_for_display,
    typical_burn_rate,
)
from tokenscope.ccusage import CcusageError
from tokenscope.models import BlockEntry
from tokenscope.navigation import Navigation
from tokenscope.query import Query
from tokenscope.ui.charts import live_spend_trajectory
from tokenscope.ui.sidebar import SidebarState

REFRESH_SECONDS = config.LIVE_REFRESH_SECONDS

# session_state key for the in-session sample history that gives the
# spend-trajectory chart's solid line real intra-block data points
# instead of a straight line from window-start to "now".
_SAMPLES_KEY = "live-trajectory-samples"


def render(state: SidebarState, nav: Navigation) -> None:
    """Live view shell: H1 + subtitle, then the fragment-refreshed
    panel for everything else."""
    st.markdown("# Live")
    st.caption("Real-time snapshot of the current 5-hour billing window.")
    _live_panel(offline=state.query.offline, tz=state.query.tz)


@st.fragment(run_every=REFRESH_SECONDS)
def _live_panel(offline: bool, tz: str | None = None) -> None:
    """Auto-refreshing live panel. Args must be hashable so Streamlit
    can key the fragment; bool + str are fine."""
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
        st.info(
            "No active billing block right now. Start a Claude Code "
            "session to see live spend."
        )
        return

    typical = typical_burn_rate(report)
    _render_window_banner(active, tz=tz)
    _render_kpis(active, typical=typical)
    _render_spend_trajectory(active, now_utc=now_utc)


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


def _render_window_banner(active: BlockEntry, *, tz: str | None) -> None:
    """Two-line banner under the H1 with the active block's context.

    Line 1: time range + minutes remaining.
    Line 2: models active in the block.

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
    remaining_part = (
        f" · {minutes_remaining} min remaining"
        if minutes_remaining is not None
        else ""
    )

    models = (
        ", ".join(active.models)
        if active.models
        else UNKNOWN_MODEL_FAMILY
    )

    st.markdown(
        f"""
        <div class="tokenscope-live-banner">
          <div class="tokenscope-live-banner-row">
            <strong>Active block</strong>
            · {start_disp} – {end_disp} {tz_label}{remaining_part}
          </div>
          <div class="tokenscope-live-banner-row tokenscope-live-banner-sub">
            Models in use: {models}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- KPIs ---------------------------------------------------------------


def _render_kpis(active: BlockEntry, *, typical: float | None) -> None:
    """Four-card KPI row matching the Overview look. Every card has a
    value + a one-line plain-English caption — no formula captions,
    no help icons (the labels are self-explanatory; cache_hit-style
    surprises don't apply on this view).

    `45 min left` moved OUT of the Projected-total card and into the
    window banner above — minutes remaining is a property of the
    window, not the projected cost.
    """
    c1, c2, c3, c4 = st.columns(4)

    with c1, st.container(border=True):
        st.metric("Cost so far", f"${active.cost_usd:,.2f}")
        st.caption("this 5-hour block")

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
        if active.projection is not None:
            st.metric(
                "Projected total",
                f"${active.projection.total_cost:,.2f}",
            )
            st.caption("at the current rate")
        else:
            st.metric("Projected total", "—")
            st.caption("no projection")


# --- spend trajectory chart ---------------------------------------------


def _record_sample(active: BlockEntry, now_utc: datetime) -> list[tuple[str, float]]:
    """Append the current cost snapshot to session_state, keyed by
    block id so a new billing block starts the history fresh.

    Returns the post-update sample list as `(iso_timestamp, cost)`
    tuples — what the chart builder expects.
    """
    samples_by_block: dict[str, list[dict]] = st.session_state.setdefault(
        _SAMPLES_KEY, {}
    )
    block_samples = samples_by_block.setdefault(active.id, [])
    now_iso = now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
    # Append only when cost has actually changed — otherwise the
    # chart accumulates a horizontal cluster of identical y-values.
    if not block_samples or block_samples[-1]["cost"] != active.cost_usd:
        block_samples.append({"t": now_iso, "cost": active.cost_usd})
        samples_by_block[active.id] = block_samples
    return [(s["t"], s["cost"]) for s in block_samples]


def _render_spend_trajectory(active: BlockEntry, *, now_utc: datetime) -> None:
    """Cumulative-spend line with dashed projection to window end.

    Replaces the gauge. The chart shows where we are in the block
    visually (X axis = time across the 5-hour window) and where
    we're heading (dashed projection). The slope of the actual line
    is the burn rate — users see acceleration directly.

    Wrapped in a bordered container so the chart reads as a card
    matching the KPI strip + the Overview chart cards.
    """
    samples = _record_sample(active, now_utc)
    now_iso = now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")

    with st.container(border=True):
        st.markdown("### Spend in this block")
        st.caption(
            "Cumulative cost from the start of the window. Solid line "
            "is actual; dotted line is the projection to window end "
            "at the current rate."
        )
        fig = live_spend_trajectory(active, samples, now_iso=now_iso)
        if fig is None:
            st.caption("No projection available for this block yet.")
            return
        st.plotly_chart(
            fig, width="stretch", key="live-spend-trajectory"
        )
        if active.projection is not None:
            st.caption(
                f"Projected total tokens: "
                f"{format_compact_int(active.projection.total_tokens)} "
                f"({active.projection.total_tokens:,})"
            )
