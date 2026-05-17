"""Cache view: hit-ratio + effective per-token rate over the window.

Slice 12 reworked this page after the "$ saved" framing produced
stupidly-high numbers in the headline. The old metric multiplied
cache-read tokens by the *full* input rate, which assumed every cached
token would otherwise have been re-sent as fresh input — a hypothetical
that's never true. For a user with 3B cache reads / month, that math
gave $45k "saved" against a real spend of $2k, which is noise not
insight.

The new framing uses ccusage's *actual* numbers, no hypotheticals:

- **Cache hit ratio** — fraction of input-side tokens served from cache.
- **Effective rate ($/MTok blended)** — total window cost divided by
  total tokens. Compared to a model's published input rate ($15/MTok
  for opus, $3 for sonnet, $1 for haiku) this shows directly how much
  caching shrunk your effective per-token cost.
- **Cache hit ratio over time** — per-day version of the headline KPI.
"""

from __future__ import annotations

import streamlit as st

from tokenscope.analytics import (
    aggregate_cache_hit_ratio,
    window_effective_per_mtok,
)
from tokenscope.navigation import Navigation
from tokenscope.ui._data import load_daily
from tokenscope.ui._nav import handle_chart_drill
from tokenscope.ui.charts import cache_hit_ratio_line
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    daily_report = load_daily(state)
    if daily_report is None:
        return

    aggregate = aggregate_cache_hit_ratio(daily_report)
    effective = window_effective_per_mtok(daily_report)

    c1, c2 = st.columns(2)
    c1.metric(
        "Cache hit ratio (window)",
        f"{aggregate:.1%}",
        help="Fraction of input-side tokens served from cache rather than "
             "re-sent fresh. Higher is better — more caching means fewer "
             "tokens are paid at full input rate.",
    )
    c2.metric(
        "Effective rate ($ / 1M tokens)",
        f"${effective:,.3f}" if effective is not None else "—",
        help="Your actual blended cost per 1M tokens for this window. "
             "Compare to a model's published input rate (~$15/MTok for opus, "
             "$3 for sonnet, $1 for haiku) — caching pulls this number down.",
    )

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date range** "
            "in the sidebar, or clearing the **Project** filter if one is set."
        )
        return

    st.subheader("Cache hit ratio over time")
    fig = cache_hit_ratio_line(daily_report)
    if fig is not None:
        event = st.plotly_chart(
            fig,
            width="stretch",
            key="cache-hit-ratio-line",
            on_select="rerun",
            selection_mode=("points",),
        )
        handle_chart_drill(
            event, lambda x: nav.to_day(x[:10]), chart_key="cache-hit-ratio-line"
        )
