"""Breadcrumb trail rendered at the top of every drill-down view.

Two roles in one component:

1. Show the user where they are (`Overview › 2026-05-16 › sess-abc`).
2. Give them a one-click route home. The first crumb is rendered with an
   "← " prefix so it reads as the back affordance, not as decoration.

The previous implementation used `type="tertiary"` buttons that looked
like plain text and hid themselves entirely when the trail had only one
entry (which happens, e.g., on `?view=day` with no `day` param — the
user landed with no exit). Both fixed here.
"""

from __future__ import annotations

import streamlit as st

from tokenscope.navigation import Navigation
from tokenscope.ui._nav import route_to


def render(nav: Navigation) -> None:
    trail = nav.trail()

    # Even with one crumb (e.g. `?view=day` and no `day`), we still need
    # to give the user a way home. Synthesise the leading "← Overview"
    # button so the exit is always visible.
    if len(trail) == 1 and nav.view != "overview":
        if st.button("← Overview", key="crumb-back-only", type="secondary"):
            route_to(Navigation(view="overview"))
        return
    if len(trail) <= 1:
        return

    crumb_cols = st.columns(len(trail) * 2 - 1)
    for idx, (label, target) in enumerate(trail):
        col = crumb_cols[idx * 2]
        is_last = idx == len(trail) - 1
        is_first = idx == 0
        # The leading crumb is the "Back" affordance — prefix with "←" so
        # users read it as such, not as just a path component.
        display = f"← {label}" if is_first else label
        if is_last:
            col.markdown(f"**{display}**")
        else:
            # Secondary (was tertiary): renders with a visible border so it
            # actually looks clickable.
            if col.button(display, key=f"crumb-{idx}-{label}", type="secondary"):
                route_to(target)
        if not is_last:
            crumb_cols[idx * 2 + 1].markdown("›")


