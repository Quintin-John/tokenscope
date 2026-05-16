"""Breadcrumb trail rendered at the top of every drill-down view.

Each crumb is a button — clicking it rewrites `st.query_params` to the
crumb's target navigation, which triggers a rerun and lands the user on
that view. The current crumb is shown as plain text.
"""

from __future__ import annotations

import streamlit as st

from tokenscope.navigation import Navigation


def render(nav: Navigation) -> None:
    trail = nav.trail()
    if len(trail) <= 1:
        return

    crumb_cols = st.columns(len(trail) * 2 - 1)
    for idx, (label, target) in enumerate(trail):
        col = crumb_cols[idx * 2]
        is_last = idx == len(trail) - 1
        if is_last:
            col.markdown(f"**{label}**")
        else:
            if col.button(label, key=f"crumb-{idx}-{label}", type="tertiary"):
                _navigate(target)
        if not is_last:
            crumb_cols[idx * 2 + 1].markdown("›")


def _navigate(target: Navigation) -> None:
    """Replace st.query_params with the target's params and rerun.

    Clearing first prevents stale fields (e.g. block= when navigating up
    to session) from sticking around.
    """
    st.query_params.clear()
    for k, v in target.to_params().items():
        st.query_params[k] = v
    st.rerun()
