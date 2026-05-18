"""Day-detail view: KPIs, cost-by-model donut, sessions on day, blocks on day."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from tokenscope import data
from tokenscope.analytics import (
    blocks_on_day,
    find_daily_entry,
    sessions_on_day,
)
from tokenscope.ccusage import CcusageError
from tokenscope.log import get_logger
from tokenscope.models import BlockEntry, SessionEntry
from tokenscope.navigation import Navigation
from tokenscope.ui import breadcrumbs
from tokenscope.ui._nav import route_to
from tokenscope.ui.charts import donut_cost_by_model
from tokenscope.ui.sidebar import SidebarState

_log = get_logger(__name__)


def render(state: SidebarState, nav: Navigation) -> None:
    # Render the breadcrumb FIRST so even an incomplete URL
    # (`?view=day` with no `day` param) shows a back affordance.
    breadcrumbs.render(nav)
    if not nav.day:
        st.warning("No day selected. Click a day in the Overview charts or "
                   "use the breadcrumb above to go back.")
        return

    st.subheader(f"Day detail — {nav.day}")

    # Three ccusage fetches run concurrently on cold cache — wall-clock
    # latency drops from sum to max. On hot cache (within the
    # @st.cache_data TTL at data.py:50) all three are dict-lookup cheap;
    # the executor's overhead is the only cost and remains sub-ms.
    #
    # ANY raising CcusageError → the first error encountered when
    # reading futures in order surfaces via the existing except branch
    # (st.error + return). The other two futures may have already
    # completed; their results are discarded. The ThreadPoolExecutor's
    # __exit__ waits for in-flight workers before the function returns,
    # so no thread leaks past the render boundary.
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            daily_future = pool.submit(data.daily, state.query)
            session_future = pool.submit(data.session, state.query)
            blocks_future = pool.submit(
                data.blocks, active=False, query=state.query
            )
            daily_report = daily_future.result()
            session_report = session_future.result()
            blocks_report = blocks_future.result()
    except CcusageError as exc:
        st.error(f"ccusage failed:\n\n```\n{exc}\n```")
        return

    entry = find_daily_entry(daily_report, nav.day)
    if entry is None:
        st.info(
            f"No usage recorded on `{nav.day}` in the current window. "
            "If you meant a different day, widen the **Date range** in the "
            "sidebar — the day must be inside the window for ccusage to "
            "include it. Use the breadcrumb above to go back to Overview."
        )
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Cost", f"${entry.total_cost:,.2f}")
    c2.metric("Total tokens", f"{entry.total_tokens:,}")
    c3.metric("Models used", str(len(entry.models_used)))

    st.divider()

    left, right = st.columns([1, 1])

    with left:
        st.markdown("**Cost share by model**")
        fig = donut_cost_by_model(entry)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No model breakdown for this day.")

    with right:
        st.markdown("**Sessions touched on this day**")
        sessions = sessions_on_day(session_report, nav.day)
        if not sessions:
            st.caption("No sessions whose last activity was on this day.")
        else:
            for session in sessions:
                _session_row(session, nav)

    st.markdown("**Blocks starting on this day** (UTC)")
    blocks = blocks_on_day(blocks_report, nav.day, tz=state.query.tz)
    if not blocks:
        st.caption("No blocks started on this day.")
    else:
        for block in blocks:
            _block_row(block, nav)


def _entity_row(
    *,
    id_label: str,
    cost: float,
    tokens: int,
    button_label: str,
    button_key: str,
    nav_target: Navigation,
) -> None:
    """One drill-down row in the day-detail view.

    `_session_row` and `_block_row` previously duplicated this exact
    layout (four columns, three markdowns, one secondary button) and
    differed only in the values and the nav target. The helper takes
    pre-formatted values so the caller owns entity-specific formatting
    (e.g. the block's "— **active**" suffix) without leaking entity
    types into here.

    `nav_target` is constructed eagerly by the caller — Navigation is a
    pure dataclass with no I/O cost, so deferring it via a callable
    would add complexity without saving any work.
    """
    cols = st.columns([5, 2, 2, 2])
    cols[0].markdown(id_label)
    cols[1].markdown(f"${cost:,.2f}")
    cols[2].markdown(f"{tokens:,} tok")
    if cols[3].button(button_label, key=button_key, type="secondary"):
        _log.info("day.entity_open button=%r key=%s", button_label, button_key)
        route_to(nav_target)


def _session_button_key(session: SessionEntry) -> str:
    """Streamlit widget key for the per-session `Open session` button
    on the day view.

    `session.session_id` ALONE is not unique across projects: Claude
    Code creates a `subagents/` directory per project, and ccusage
    slugs each directory as `sessionId="subagents"`. A user with
    two projects that ran subagents will have two `SessionEntry`
    instances with the same `session_id` but different
    `project_path` — composing the key from the
    `(project_path, session_id)` tuple is unique within the user's
    SessionReport and deterministic across reruns (both fields are
    immutable on a `SessionEntry`).

    Pre-fix the key was `f"open-session-{session_id}"`, which
    collided on the duplicated id and produced a
    StreamlitDuplicateElementKey crash on the day view as soon as
    two `subagents` sessions appeared in `sessions_on_day`.
    """
    return f"open-session-{session.project_path}-{session.session_id}"


def _session_row(session: SessionEntry, nav: Navigation) -> None:
    _entity_row(
        id_label=f"`{session.session_id}`",
        cost=session.total_cost,
        tokens=session.total_tokens,
        button_label="Open session",
        button_key=_session_button_key(session),
        # `project_path` is the disambiguator for sessions sharing
        # `session_id` (the `subagents`-per-project case). Without
        # it the resulting URL would route to the first matching
        # session in the report — possibly the wrong project's row.
        nav_target=nav.to_session(
            session.session_id, session.project_path
        ),
    )


def _block_row(block: BlockEntry, nav: Navigation) -> None:
    active_suffix = " — **active**" if block.is_active else ""
    _entity_row(
        id_label=f"`{block.id}`{active_suffix}",
        cost=block.cost_usd,
        tokens=block.total_tokens,
        button_label="Open block",
        button_key=f"open-block-{block.id}",
        nav_target=nav.to_block(block.id),
    )


