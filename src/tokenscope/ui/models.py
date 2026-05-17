"""Models view — KPIs + per-model breakdown table + composition chart.

Slice 10 rework. Replaces the previous single-Sankey-with-no-context page
with three complementary surfaces:

1. **KPI row** (Total cost / Total tokens / $/MTok blended / # models).
   Anchors what the window contains before any chart loads.
2. **Per-model breakdown table** with full model names (not collapsed to
   family) so opus-4-6 vs opus-4-7 are visible. Sorted by cost desc;
   shows $/MTok blended and share of window cost.
3. **Composition chart**: a Sankey of token-kind → family for windows
   with two-or-more families; a single-family per-kind log bar otherwise
   (because a Sankey with one right node is just a comb).

Plus a small cost-share donut alongside the Sankey when there's more
than one family — same widget Day-detail already uses, aggregated over
the whole window.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    available_models,
    filter_daily_by_models,
    model_breakdown,
)
from tokenscope.ccusage import CcusageError
from tokenscope.navigation import Navigation
from tokenscope.ui.charts import (
    single_family_token_bar,
    token_flow_sankey,
)
from tokenscope.ui.sidebar import SidebarState


def render(state: SidebarState, nav: Navigation) -> None:
    if (banner := state.plan.banner_text()) is not None:
        st.info(banner)

    try:
        daily_report = data.daily(state.query)
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    all_models = available_models(daily_report)
    chosen = set(state.selected_models)
    if chosen and chosen != set(all_models):
        daily_report = filter_daily_by_models(daily_report, chosen)

    if not daily_report.daily:
        st.info(
            "No usage in the selected window. Try widening the **Date range** "
            "in the sidebar, or clearing the **Project** filter if one is set."
        )
        return

    rows = model_breakdown(daily_report)
    _render_kpis(rows)
    st.divider()

    _render_breakdown_table(rows)
    st.divider()

    _render_composition(daily_report, rows)


def _render_kpis(rows: list[dict]) -> None:
    total_cost = sum(r["cost"] for r in rows)
    total_tokens = sum(r["tokens"] for r in rows)
    blended_per_mtok = (
        total_cost / total_tokens * 1_000_000 if total_tokens else 0.0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total cost", f"${total_cost:,.2f}")
    c2.metric("Total tokens", f"{total_tokens:,}")
    c3.metric(
        "$ / 1M tokens (blended)",
        f"${blended_per_mtok:,.3f}",
        help="Window total cost divided by total tokens × 1,000,000. "
             "Blends input, output, cache_create, cache_read across every model.",
    )
    c4.metric("Models in window", str(len(rows)))


def _render_breakdown_table(rows: list[dict]) -> None:
    st.markdown("**Model breakdown**")
    if not rows:
        st.caption("No model breakdowns in the selected window.")
        return
    df = pd.DataFrame(rows)
    # Order columns for readability; keep raw names in source for the donut/sankey.
    display = df[["model", "family", "cost", "tokens", "per_mtok", "share"]].rename(
        columns={
            "model": "Model",
            "family": "Family",
            "cost": "Cost (USD)",
            "tokens": "Tokens",
            "per_mtok": "$ / 1M tokens",
            "share": "Share of cost",
        }
    )
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "Cost (USD)": st.column_config.NumberColumn(format="$%.2f"),
            "Tokens": st.column_config.NumberColumn(format="%d"),
            "$ / 1M tokens": st.column_config.NumberColumn(format="$%.3f"),
            "Share of cost": st.column_config.ProgressColumn(
                min_value=0.0, max_value=1.0, format="%.1f%%"
            ),
        },
    )


def _render_composition(daily_report, rows: list[dict]) -> None:
    families = {row["family"] for row in rows}
    if len(families) <= 1:
        st.markdown("**Token composition**")
        family = next(iter(families), "")
        st.caption(
            f"Only one model family in this window ({family or 'unknown'}). "
            "Showing total tokens by kind instead of a Sankey — a flow "
            "diagram with one destination is just a labelled bar."
        )
        fig = single_family_token_bar(daily_report)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        return

    st.markdown("**Token flow: kind → model family**")
    st.caption(
        "How your token traffic splits across model families. Numbers next "
        "to each family = total cost this window. Hover any band to see "
        "tokens and cost together."
    )

    # Segmented controls — only useful when there are 2+ families.
    control_cols = st.columns([2, 2, 6])
    width_mode = control_cols[0].segmented_control(
        "Width represents",
        options=["Tokens", "Cost"],
        default="Tokens",
        key="models-sankey-width-mode",
    ) or "Tokens"
    top_n_label = control_cols[1].segmented_control(
        "Show",
        options=["Top 3", "Top 5", "Top 10", "All"],
        default="Top 5" if len(families) > 5 else "All",
        key="models-sankey-top-n",
    ) or "All"
    top_n = (
        None
        if top_n_label == "All"
        else int(top_n_label.split()[-1])
    )
    value_mode = "cost" if width_mode == "Cost" else "tokens"

    left, right = st.columns([2, 1])
    with left:
        fig = token_flow_sankey(daily_report, value_mode=value_mode, top_n=top_n)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown("**Cost share by family**")
        family_rows = _family_donut_rows(rows)
        if family_rows:
            df = pd.DataFrame(family_rows)
            import plotly.express as px

            donut = px.pie(df, names="family", values="cost", hole=0.55)
            donut.update_traces(textposition="inside", textinfo="percent+label")
            donut.update_layout(
                margin=dict(l=10, r=10, t=10, b=10), showlegend=False, height=320
            )
            st.plotly_chart(donut, width="stretch")

    # Slice 15: drill-to-overview affordance per family. Plotly Sankey
    # node-click selection through Streamlit is finicky; explicit buttons
    # give the user a clear "drill into opus" action that piggybacks on
    # the new sidebar URL state — the model filter survives the jump.
    st.markdown("**Drill into a family**")
    st.caption(
        "Pre-fills the Models filter and routes to the Overview. "
        "Use the page selector or breadcrumbs to come back."
    )
    drill_cols = st.columns(min(len(rows), 6) or 1)
    for idx, fam in enumerate(sorted(families)):
        fam_models = [r["model"] for r in rows if r["family"] == fam]
        if not fam_models:
            continue
        col = drill_cols[idx % len(drill_cols)]
        if col.button(f"→ {fam}", key=f"drill-family-{fam}"):
            st.query_params["view"] = "overview"
            st.query_params["models"] = ",".join(fam_models)
            # Wipe deeper drill state if present.
            for k in ("day", "session", "block"):
                if k in st.query_params:
                    del st.query_params[k]
            # session_state must agree with the URL after we've set it.
            st.session_state["sidebar-models"] = fam_models
            st.rerun()


def _family_donut_rows(rows: list[dict]) -> list[dict]:
    """Roll model-level rows up to family level for the donut."""
    by_family: dict[str, float] = {}
    for r in rows:
        by_family[r["family"]] = by_family.get(r["family"], 0.0) + r["cost"]
    return [{"family": k, "cost": v} for k, v in sorted(by_family.items())]
