"""Models view — per-model spend, token-kind composition, and drill.

Replaces the prior Sankey + donut + drill-buttons layout, which had
multiple regressions: a Streamlit-API crash on drill (`session_state`
assignment to a widget-keyed slot after the widget had instantiated),
a percent-display bug in the share column (raw fraction passed
through `%.1f%%` showed `1.0%` instead of `99.96%`), an illegible
Sankey (cache_read drowning the other three kinds, no PALETTE
colors), and a useless `Models in window: N` KPI.

Page layout (top to bottom):

  * H1 + subtitle.
  * KPI strip — Total cost / Total tokens / Effective $/1M tokens /
    Cost concentration (top model's share of spend, replaces the
    `# models` count).
  * Model breakdown table — per-model cost, formatted tokens,
    blended rate, share (proper percent), cache hit ratio, last
    used. Sorted descending by cost.
  * Per-model token-kind horizontal stacked bar — what kinds each
    model is burning, with PALETTE token-kind colors matching every
    other token-mix chart in the app.
  * Drill row — one button per family, grouped, writes
    `?view=overview&models=<ids>` to `st.query_params` and reruns.
    The sidebar's unconditional URL → session_state sync on the
    `models` param picks up the change on the rerun (the prior
    `st.session_state["sidebar-models"] = …` line was the crash; the
    new flow is purely URL-driven).
"""

from __future__ import annotations

import streamlit as st

from tokenscope.analytics import (
    cost_concentration_summary,
    format_compact_int,
    model_breakdown,
    short_model_label,
)
from tokenscope.log import get_logger
from tokenscope.models import DailyReport
from tokenscope.navigation import Navigation
from tokenscope.ui._data import load_daily
from tokenscope.ui._nav import route_to
from tokenscope.ui.charts import per_model_token_kind_bar
from tokenscope.ui.sidebar import SidebarState

_log = get_logger(__name__)


def render(state: SidebarState, nav: Navigation) -> None:
    st.markdown("# Models")
    st.caption(
        "How spend and tokens are distributed across the models you use."
    )

    if (banner := state.plan.banner_text()) is not None:
        st.info(banner)

    daily_report = load_daily(state)
    if daily_report is None:
        return

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date "
            "range** in the sidebar, or clearing the **Project** filter "
            "if one is set."
        )
        return

    rows = model_breakdown(daily_report)
    _render_kpis(rows)
    _render_breakdown_table(rows)
    _render_token_kind_composition(daily_report, rows)
    _render_drill(rows)


# --- KPIs ---------------------------------------------------------------


def _render_kpis(rows: list[dict]) -> None:
    """Four-card KPI row — every card wrapped in `st.container(border=True)`
    so the strip matches Overview / Live / Cache.

    The fourth slot replaces the previous `Models in window: N`
    (a count of table rows the user can see immediately below) with
    cost-concentration framing: the top model's share of total
    spend. That answers "is one model dominating?" in a glance
    without the user reading the share column.
    """
    total_cost = sum(r["cost"] for r in rows)
    total_tokens = sum(r["tokens"] for r in rows)
    blended_per_mtok = (
        total_cost / total_tokens * 1_000_000 if total_tokens else 0.0
    )
    concentration = cost_concentration_summary(rows)

    c1, c2, c3, c4 = st.columns(4)
    with c1, st.container(border=True):
        st.metric("Total cost", f"${total_cost:,.2f}")
        st.caption("across every model in the window")

    with c2, st.container(border=True):
        st.metric("Total tokens", format_compact_int(total_tokens))
        st.caption(f"{total_tokens:,} tokens")

    with c3, st.container(border=True):
        st.metric(
            "Effective $ / 1M tokens",
            f"${blended_per_mtok:,.3f}",
            help=(
                "Blended cost across every model in the window: "
                "total cost ÷ total tokens × 1,000,000."
            ),
        )
        st.caption("blended across all models")

    with c4, st.container(border=True):
        if concentration is None:
            st.metric("Top model", "—")
            st.caption("no models in this window")
        else:
            st.metric(
                "Top model",
                short_model_label(concentration["model"]),
            )
            st.caption(
                f"{concentration['share']:.2%} of spend"
            )


# --- breakdown table ----------------------------------------------------


def _render_breakdown_table(rows: list[dict]) -> None:
    """Per-model table wrapped in a bordered card. Columns:
    Model · Family · Cost · Tokens · $/1M · Cache hit · Last used · Share.

    Display-shape transformations:

      * `share` is converted from a 0–1 fraction to a 0–100 percent
        so the ProgressColumn fills correctly AND the label shows
        the right number (the prior `%.1f%%` format applied to the
        fraction rendered `1.0%` for opus's 99.96% share — the
        format wasn't multiplying by 100, the fraction was).
      * `tokens` is abbreviated via `format_compact_int`
        (1,374,041,578 → "1.37B"); the raw integer remains in
        `tokens_raw` for the tooltip.
      * `cache_hit_ratio` formatted as percent.
    """
    with st.container(border=True):
        st.markdown("### Model breakdown")
        st.caption("Sorted by cost descending.")
        if not rows:
            st.caption("No model breakdowns in the selected window.")
            return
        display_rows = [
            {
                "Model": row["model"],
                "Family": row["family"],
                "Cost (USD)": row["cost"],
                "Tokens": format_compact_int(row["tokens"]),
                "$ / 1M tokens": row["per_mtok"],
                "Cache hit": row["cache_hit_ratio"],
                "Last used": row["last_used"],
                "Share of cost": row["share"] * 100.0,
            }
            for row in rows
        ]
        st.dataframe(
            display_rows,
            width="stretch",
            hide_index=True,
            column_config={
                "Cost (USD)": st.column_config.NumberColumn(format="$%.2f"),
                "$ / 1M tokens": st.column_config.NumberColumn(
                    format="$%.3f"
                ),
                "Cache hit": st.column_config.NumberColumn(format="%.1f%%"),
                "Share of cost": st.column_config.ProgressColumn(
                    min_value=0.0, max_value=100.0, format="%.2f%%"
                ),
            },
        )


# --- per-model token-kind composition -----------------------------------


def _render_token_kind_composition(
    daily_report: DailyReport, rows: list[dict]
) -> None:
    """Horizontal stacked bar: per model, four colour-coded segments
    showing input / output / cache_create / cache_read token counts.

    Replaces the Sankey. The prior Sankey rendered cache_read as
    ~95% of horizontal width, with the other three kinds squashed
    to 1-pixel slivers with overlapping labels — which is
    structurally what a Sankey does when one link dominates, and
    no amount of "Top N" controls fixes. This chart sidesteps the
    problem by stacking horizontally per model with consistent
    X-axis scaling per row (one model's bar maxes out at its own
    total tokens; comparisons are visual).
    """
    with st.container(border=True):
        st.markdown("### Per-model token-kind composition")
        st.caption(
            "For each model, what kinds of tokens are being burned. "
            "Same hues as the Overview / Cache token-mix charts — "
            "input is pink, output blue, cache_create amber, "
            "cache_read teal."
        )
        if not rows:
            st.caption("No models with token activity in this window.")
            return
        fig = per_model_token_kind_bar(daily_report)
        if fig is None:
            st.caption("No models with token activity in this window.")
            return
        st.plotly_chart(
            fig, width="stretch", key="models-token-kind"
        )


# --- drill --------------------------------------------------------------


def _render_drill(rows: list[dict]) -> None:
    """One button per family, grouped in a single row. Clicking
    writes `?view=overview&models=<comma-separated-model-ids>` to
    `st.query_params` and reruns. The sidebar's unconditional URL
    → session_state sync on the `models` param picks up the new
    selection and the multiselect re-renders with the family
    pre-filtered.

    The PRIOR implementation set `st.session_state["sidebar-models"]
    = fam_models` directly — that crashed because the sidebar
    widget had already instantiated this run, and Streamlit forbids
    assignment to a widget-keyed slot after instantiation. The new
    flow goes purely through URL state; no session_state mutation
    from this view.
    """
    families: dict[str, list[str]] = {}
    for row in rows:
        families.setdefault(row["family"], []).append(row["model"])
    if not families:
        return

    with st.container(border=True):
        st.markdown("### Drill into a family")
        cols = st.columns(min(len(families), 4) or 1)
        for idx, family in enumerate(sorted(families)):
            fam_models = families[family]
            col = cols[idx % len(cols)]
            if col.button(
                f"View {family} in Overview →",
                key=f"drill-family-{family}",
                width="stretch",
            ):
                _log.info(
                    "models.family_drill family=%s model_count=%d",
                    family,
                    len(fam_models),
                )
                route_to(
                    Navigation(view="overview"),
                    extra_params={"models": ",".join(fam_models)},
                )
