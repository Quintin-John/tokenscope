"""Active-block live view: burn gauge + projection, auto-refreshing every 30s.

Phase 6 deliverable per PLAN.md §6:
    "Auto-refresh `--active` block every 30s (`st.fragment(run_every="30s")`),
     burn gauge + projection."

Slice 12 additions: "Last refreshed at HH:MM:SS" caption (you can tell
the panel is alive) and a "typical burn" threshold line on the gauge
computed from the median cost-per-hour of recent completed blocks.

Implementation notes:
- The refreshing panel is a `@st.fragment(run_every=30)`. Only the panel
  re-runs on the timer — the page selector, sidebar, and breadcrumbs are
  not affected.
- The live ccusage call bypasses `tokenscope.data` (which is wrapped in
  `@st.cache_data(ttl=30)`). Compounding two 30s windows would give the
  user a snapshot up to a minute stale, which defeats "live". The
  fragment is the only refresh cadence.
- The sidebar's date/project/model filters don't apply to "right now";
  the only sidebar control we honour is `offline`, so an offline-pinned
  session keeps using cached pricing in the live view too.
- We fetch ALL blocks (not just `--active`) and filter for the active
  block in Python. Single ccusage call serves both the headline KPIs
  and the typical-burn baseline.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from tokenscope import ccusage
from tokenscope.analytics import typical_burn_rate
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.query import Query
from tokenscope.ui.charts import burn_gauge
from tokenscope.ui.sidebar import SidebarState


REFRESH_SECONDS = 30


def render(state: SidebarState, nav: Navigation) -> None:
    st.subheader("Active billing block (live)")
    st.caption(
        f"Auto-refreshes every {REFRESH_SECONDS}s. Ignores the date / project / "
        "model filters — this view is a real-time snapshot of the current "
        "5-hour billing window."
    )

    _live_panel(offline=state.query.offline, tz=state.query.tz)


@st.fragment(run_every=REFRESH_SECONDS)
def _live_panel(offline: bool, tz: str | None = None) -> None:
    """The actual live panel. Args must be hashable so Streamlit can key the
    fragment; a bool + string are fine."""
    refreshed_at = datetime.now()
    try:
        # Fetch all blocks so we can both pick the active one and compute
        # the typical-burn baseline from completed blocks in a single call.
        report = ccusage.blocks(active=False, query=Query(offline=offline, tz=tz))
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    active = next((b for b in report.blocks if b.is_active), None)
    typical = typical_burn_rate(report)
    st.caption(
        f"Last refreshed at **{refreshed_at.strftime('%H:%M:%S')}** "
        f"({REFRESH_SECONDS}s cadence)."
    )

    if active is None:
        st.info(
            "No active billing block right now. Start a Claude Code session "
            "to see the live burn gauge."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cost so far", f"${active.cost_usd:,.2f}")
    if active.burn_rate is not None:
        burn_kwargs: dict = {}
        if typical is not None:
            change = (active.burn_rate.cost_per_hour - typical) / typical
            burn_kwargs["delta"] = f"{change:+.0%} vs typical"
        c2.metric(
            "$/hr",
            f"${active.burn_rate.cost_per_hour:,.2f}",
            help=(
                f"Median burn over recent completed blocks: ${typical:,.2f}/hr."
                if typical is not None
                else "Need 3+ completed blocks to compute a typical baseline."
            ),
            **burn_kwargs,
        )
        c3.metric(
            "Tokens / min",
            f"{active.burn_rate.tokens_per_minute:,.0f}",
            help="Indicator-weighted tokens per minute, from ccusage's burnRate.",
        )
    else:
        c2.metric("$/hr", "—")
        c3.metric("Tokens / min", "—")
    if active.projection is not None:
        c4.metric(
            "Projected total",
            f"${active.projection.total_cost:,.2f}",
            delta=f"{active.projection.remaining_minutes} min left",
            delta_color="off",
            help="Cost projected to the end of this 5-hour window at the current burn rate.",
        )
    else:
        c4.metric("Projected total", "—")

    if tz:
        from tokenscope.tz import utc_iso_to_local

        start_disp = utc_iso_to_local(active.start_time, tz) or active.start_time
        end_disp = utc_iso_to_local(active.end_time, tz) or active.end_time
        st.caption(
            f"Window {start_disp} → {end_disp}. "
            f"Models: {', '.join(active.models) or '—'}."
        )
    else:
        st.caption(
            f"Window {active.start_time} → {active.end_time} (UTC). "
            f"Models: {', '.join(active.models) or '—'}."
        )

    gauge = burn_gauge(active, typical=typical)
    if gauge is not None:
        st.plotly_chart(gauge, width="stretch")

    if active.projection is not None:
        with st.expander("Projection detail"):
            p = active.projection
            c1, c2, c3 = st.columns(3)
            c1.metric("Projected total cost", f"${p.total_cost:,.2f}")
            c2.metric("Projected total tokens", f"{p.total_tokens:,}")
            c3.metric("Minutes remaining", str(p.remaining_minutes))
