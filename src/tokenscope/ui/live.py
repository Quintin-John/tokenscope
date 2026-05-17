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
    block_cache_hit_ratio,
    block_cost_by_kind,
    build_intra_block_token_throughput,
    format_compact_int,
    format_timezone_for_display,
    typical_burn_rate,
)
from tokenscope.ccusage import CcusageError
from tokenscope.models import BlockEntry
from tokenscope.navigation import Navigation
from tokenscope.query import Query
from tokenscope.ui.charts import (
    PALETTE,
    live_spend_trajectory,
    live_token_throughput,
)
from tokenscope.ui.sidebar import SidebarState

# Display labels for the four token kinds. Match the PALETTE keys so
# the swatch lookup is the same string used to colour every other
# chart trace named for that kind.
_TOKEN_KIND_LABELS: dict[str, str] = {
    "input": "Input",
    "output": "Output",
    "cache_create": "Cache create",
    "cache_read": "Cache read",
}
_TOKEN_KIND_ORDER: tuple[str, ...] = (
    "input",
    "output",
    "cache_create",
    "cache_read",
)

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
    cost_samples, kind_samples = _record_sample(active, now_utc)
    _render_token_kind_kpis(active)
    _render_cache_hit_callout(active)
    now_iso = _now_iso(now_utc)
    _render_spend_trajectory(active, cost_samples=cost_samples, now_iso=now_iso)
    _render_token_throughput(active, kind_samples=kind_samples, now_iso=now_iso)


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


def _now_iso(now_utc: datetime) -> str:
    """ISO-8601 "now" string in the format the chart layer expects
    (`...Z` suffix, second precision). Centralised so the spend and
    throughput charts get the same string for their now-reference
    line — drift between the two would put the dotted line at
    slightly different x values."""
    return now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def _record_sample(
    active: BlockEntry, now_utc: datetime
) -> tuple[list[tuple[str, float]], list[tuple[str, dict[str, int]]]]:
    """Append the current snapshot to session_state, keyed by block
    id so a new billing block starts the history fresh.

    Each sample carries BOTH the cumulative cost (drives the spend
    trajectory chart) and the cumulative per-kind token counts
    (drive the token throughput chart). Storing both in one record
    means the two charts share a single sample cadence — no risk
    of the spend line and the throughput area disagreeing about
    which "now" point they end at.

    Returns ``(cost_samples, kind_samples)`` as the tuple shapes
    each chart builder expects:

      * ``cost_samples`` — `(iso_timestamp, cost)` tuples
      * ``kind_samples`` — `(iso_timestamp, {kind: count})` tuples
    """
    samples_by_block: dict[str, list[dict]] = st.session_state.setdefault(
        _SAMPLES_KEY, {}
    )
    block_samples = samples_by_block.setdefault(active.id, [])
    now_iso = _now_iso(now_utc)
    counts = {
        "input": active.token_counts.input_tokens,
        "output": active.token_counts.output_tokens,
        "cache_create": active.token_counts.cache_creation_input_tokens,
        "cache_read": active.token_counts.cache_read_input_tokens,
    }
    last = block_samples[-1] if block_samples else None
    if last is None or last["cost"] != active.cost_usd or last["counts"] != counts:
        block_samples.append(
            {"t": now_iso, "cost": active.cost_usd, "counts": counts}
        )
        samples_by_block[active.id] = block_samples
    cost_samples = [(s["t"], s["cost"]) for s in block_samples]
    kind_samples = [(s["t"], s["counts"]) for s in block_samples]
    return cost_samples, kind_samples


def _render_spend_trajectory(
    active: BlockEntry,
    *,
    cost_samples: list[tuple[str, float]],
    now_iso: str,
) -> None:
    """Cumulative-spend line with dashed projection to window end.

    Replaces the gauge. The chart shows where we are in the block
    visually (X axis = time across the 5-hour window) and where
    we're heading (dashed projection). The slope of the actual line
    is the burn rate — users see acceleration directly.

    Wrapped in a bordered container so the chart reads as a card
    matching the KPI strip + the Overview chart cards.
    """
    with st.container(border=True):
        st.markdown("### Spend in this block")
        st.caption(
            "Cumulative cost from the start of the window. Solid line "
            "is actual; dotted line is the projection to window end "
            "at the current rate. The vertical dotted reference line "
            "marks where 'now' falls inside the block."
        )
        fig = live_spend_trajectory(active, cost_samples, now_iso=now_iso)
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
    counts = {
        "input": active.token_counts.input_tokens,
        "output": active.token_counts.output_tokens,
        "cache_create": active.token_counts.cache_creation_input_tokens,
        "cache_read": active.token_counts.cache_read_input_tokens,
    }

    cols = st.columns(len(_TOKEN_KIND_ORDER))
    for col, kind in zip(cols, _TOKEN_KIND_ORDER):
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
    fifth raw count".
    """
    ratio = block_cache_hit_ratio(active)
    pct = ratio * 100
    st.markdown(
        f"""
        <div class="tokenscope-cache-ratio-callout">
          <div class="tokenscope-cache-ratio-value">{pct:.1f}%</div>
          <div class="tokenscope-cache-ratio-label">
            Cache hit ratio · cache_read ÷ (input + cache_create + cache_read)
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_token_throughput(
    active: BlockEntry,
    *,
    kind_samples: list[tuple[str, dict[str, int]]],
    now_iso: str,
) -> None:
    """Percent-stacked area of per-interval token-kind throughput.

    Visually paired with the spend trajectory above: same X-axis
    window, same now-reference line. Where spend tells you "how
    much"; throughput tells you "of what kind".

    When no intra-block intervals carry token activity (a very
    new block, or one that hasn't moved since the page opened),
    `build_intra_block_token_throughput` returns an empty list
    and we render an empty-state caption instead of a frame.
    """
    with st.container(border=True):
        st.markdown("### Token throughput in this block")
        st.caption(
            "Per-interval mix of input / output / cache_create / "
            "cache_read tokens. Each column sums to 100%; hover for "
            "absolute counts."
        )
        rows = build_intra_block_token_throughput(
            active, kind_samples, now_iso=now_iso
        )
        fig = live_token_throughput(active, rows, now_iso=now_iso)
        if fig is None:
            st.caption(
                "No intra-block intervals with token activity yet — "
                "keep the page open and the chart populates as the "
                "block accrues."
            )
            return
        st.plotly_chart(
            fig, width="stretch", key="live-token-throughput"
        )
