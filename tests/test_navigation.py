"""Tests for tokenscope.navigation — URL state parsing + transitions."""

from __future__ import annotations

import pytest

from tokenscope.navigation import Navigation


# ---------- from_params ----------


def test_default_view_is_overview() -> None:
    nav = Navigation.from_params({})
    assert nav.view == "overview"
    assert nav.day is None
    assert nav.session is None
    assert nav.block is None


def test_parses_day_view() -> None:
    nav = Navigation.from_params({"view": "day", "day": "2026-05-16"})
    assert nav.view == "day"
    assert nav.day == "2026-05-16"


def test_parses_session_view() -> None:
    nav = Navigation.from_params(
        {"view": "session", "day": "2026-05-16", "session": "abc"}
    )
    assert nav.view == "session"
    assert nav.day == "2026-05-16"
    assert nav.session == "abc"


def test_parses_block_view() -> None:
    nav = Navigation.from_params(
        {
            "view": "block",
            "day": "2026-05-16",
            "session": "abc",
            "block": "2026-05-16T13:00:00.000Z",
        }
    )
    assert nav.view == "block"
    assert nav.block == "2026-05-16T13:00:00.000Z"


def test_invalid_view_falls_back_to_overview() -> None:
    nav = Navigation.from_params({"view": "rce", "day": "x"})
    assert nav.view == "overview"
    # Other fields are still parsed (the URL was tampered, not crashed).
    assert nav.day == "x"


def test_empty_string_fields_treated_as_none() -> None:
    nav = Navigation.from_params({"view": "day", "day": "", "session": ""})
    assert nav.day is None
    assert nav.session is None


# ---------- to_params ----------


def test_to_params_omits_empty() -> None:
    assert Navigation().to_params() == {"view": "overview"}


def test_to_params_round_trip() -> None:
    nav = Navigation(view="session", day="2026-05-16", session="abc")
    assert Navigation.from_params(nav.to_params()) == nav


def test_to_params_emits_block_field() -> None:
    nav = Navigation(
        view="block", day="2026-05-16", session="abc", block="2026-05-16T13:00:00.000Z"
    )
    params = nav.to_params()
    assert params == {
        "view": "block",
        "day": "2026-05-16",
        "session": "abc",
        "block": "2026-05-16T13:00:00.000Z",
    }


# ---------- transitions ----------


def test_to_day_drops_deeper_fields() -> None:
    nav = Navigation(view="block", day="2026-05-15", session="abc", block="b")
    new = nav.to_day("2026-05-16")
    assert new == Navigation(view="day", day="2026-05-16")


def test_to_session_preserves_day_drops_block() -> None:
    nav = Navigation(view="block", day="2026-05-16", session="old", block="b")
    new = nav.to_session("new", "/Users/q/proj-new")
    assert new == Navigation(
        view="session",
        day="2026-05-16",
        session="new",
        session_project="/Users/q/proj-new",
    )


def test_to_block_preserves_session_and_day() -> None:
    nav = Navigation(view="session", day="2026-05-16", session="abc")
    new = nav.to_block("block-x")
    assert new == Navigation(view="block", day="2026-05-16", session="abc", block="block-x")


def test_to_overview_clears() -> None:
    nav = Navigation(view="block", day="2026-05-16", session="abc", block="b")
    assert nav.to_overview() == Navigation(view="overview")


# ---------- trail ----------


def test_trail_overview_is_root_only() -> None:
    trail = Navigation(view="overview").trail()
    assert len(trail) == 1
    assert trail[0][0] == "Overview"


def test_trail_day() -> None:
    trail = Navigation(view="day", day="2026-05-16").trail()
    labels = [label for label, _ in trail]
    assert labels == ["Overview", "2026-05-16"]


def test_trail_session_under_day() -> None:
    trail = Navigation(view="session", day="2026-05-16", session="abc").trail()
    labels = [label for label, _ in trail]
    assert labels == ["Overview", "2026-05-16", "abc"]


def test_trail_block_under_session_under_day() -> None:
    trail = Navigation(
        view="block", day="2026-05-16", session="sess", block="2026-05-16T13:00:00.000Z"
    ).trail()
    labels = [label for label, _ in trail]
    assert labels[:3] == ["Overview", "2026-05-16", "sess"]
    assert labels[3] == "2026-05-16T13:00:00.000Z"


def test_trail_truncates_long_identifiers() -> None:
    long_id = "x" * 100
    trail = Navigation(view="session", day="2026-05-16", session=long_id).trail()
    assert trail[-1][0].endswith("…")
    assert len(trail[-1][0]) <= 24


# ---------- cache + models views ----------


def test_cache_view_parses() -> None:
    nav = Navigation.from_params({"view": "cache"})
    assert nav.view == "cache"


def test_models_view_parses() -> None:
    nav = Navigation.from_params({"view": "models"})
    assert nav.view == "models"


def test_cache_view_trail_is_root_only() -> None:
    # Cache is a top-level view, so it doesn't accumulate drill crumbs.
    assert Navigation(view="cache").trail() == [("Overview", Navigation(view="overview"))]


def test_top_level_views_constant() -> None:
    from tokenscope.navigation import TOP_LEVEL_VIEWS

    assert TOP_LEVEL_VIEWS == ("overview", "live", "cache", "models")


# ---------- StreamlitDuplicateElementKey Slice 2: session_project disambiguator ----------
#
# `session_id` is NOT unique across projects (ccusage slugs each
# Claude Code project's `subagents/` directory as the same id), so
# `Navigation.session_project` carries the disambiguating
# `project_path` alongside `session`. These tests pin the field's
# round-trip through `from_params` / `to_params`, the new `to_session`
# signature, and the carry-forward through `to_block` / `trail`.


def test_navigation_from_params_reads_session_project() -> None:
    """`session_project` URL param populates the corresponding
    Navigation field."""
    nav = Navigation.from_params({
        "view": "session",
        "day": "2026-05-16",
        "session": "subagents",
        "session_project": "/Users/q/projA",
    })
    assert nav.view == "session"
    assert nav.session == "subagents"
    assert nav.session_project == "/Users/q/projA"


def test_navigation_to_params_emits_session_project() -> None:
    """`to_params()` round-trips `session_project` when set."""
    nav = Navigation(
        view="session",
        day="2026-05-16",
        session="subagents",
        session_project="/Users/q/projA",
    )
    assert nav.to_params() == {
        "view": "session",
        "day": "2026-05-16",
        "session": "subagents",
        "session_project": "/Users/q/projA",
    }


def test_navigation_to_params_omits_session_project_when_absent() -> None:
    """Empty / None `session_project` doesn't leak into the URL.
    Legacy URLs without the field still serialize cleanly."""
    nav = Navigation(view="session", day="2026-05-16", session="abc")
    assert "session_project" not in nav.to_params()


def test_navigation_round_trip_preserves_session_project() -> None:
    """`Navigation → to_params → from_params` is the identity
    function for a session view with `session_project` set."""
    nav = Navigation(
        view="session",
        day="2026-05-16",
        session="subagents",
        session_project="/Users/q/projB",
    )
    assert Navigation.from_params(nav.to_params()) == nav


def test_to_session_requires_project_path_arg() -> None:
    """The compile-time signature catches the original bug at the
    call site: `to_session(session_id)` without project_path is no
    longer callable. A regression that re-adds a default would
    silently allow the ambiguous form back into production."""
    nav = Navigation(view="day", day="2026-05-16")
    with pytest.raises(TypeError):
        nav.to_session("subagents")  # type: ignore[call-arg]


def test_to_session_sets_session_project_field() -> None:
    """`to_session(session_id, project_path)` populates BOTH the
    session AND the session_project fields on the returned
    Navigation."""
    nav = Navigation(view="day", day="2026-05-16")
    new = nav.to_session("subagents", "/Users/q/projA")
    assert new.session == "subagents"
    assert new.session_project == "/Users/q/projA"


def test_to_block_carries_session_project_forward() -> None:
    """Drilling from session → block must preserve
    `session_project` — otherwise the breadcrumb back from block
    would lose the disambiguator and the session crumb would
    misroute on `subagents`-style duplicate ids."""
    nav = Navigation(
        view="session",
        day="2026-05-16",
        session="subagents",
        session_project="/Users/q/projB",
    )
    new = nav.to_block("2026-05-16T13:00:00.000Z")
    assert new.session == "subagents"
    assert new.session_project == "/Users/q/projB"
    assert new.block == "2026-05-16T13:00:00.000Z"


def test_trail_session_crumb_carries_session_project() -> None:
    """The session crumb returned by `trail()` must carry
    `session_project` in its target Navigation — clicking the crumb
    re-routes to the disambiguated session, not the first-by-id
    match."""
    nav = Navigation(
        view="session",
        day="2026-05-16",
        session="subagents",
        session_project="/Users/q/projB",
    )
    crumbs = nav.trail()
    session_crumb_target = crumbs[-1][1]
    assert session_crumb_target.session == "subagents"
    assert session_crumb_target.session_project == "/Users/q/projB"


def test_trail_block_view_session_crumb_carries_session_project() -> None:
    """Same contract when the active view is `block` — the session
    crumb between Day and Block in the trail must preserve
    `session_project` so clicking it routes back to the correct
    session."""
    nav = Navigation(
        view="block",
        day="2026-05-16",
        session="subagents",
        session_project="/Users/q/projB",
        block="2026-05-16T13:00:00.000Z",
    )
    crumbs = nav.trail()
    # crumbs: [Overview, day, session, block] — session is index 2.
    session_crumb_target = crumbs[2][1]
    assert session_crumb_target.view == "session"
    assert session_crumb_target.session == "subagents"
    assert session_crumb_target.session_project == "/Users/q/projB"


def test_live_view_parses() -> None:
    nav = Navigation.from_params({"view": "live"})
    assert nav.view == "live"


def test_live_view_trail_is_root_only() -> None:
    assert Navigation(view="live").trail() == [("Overview", Navigation(view="overview"))]


# --- Slice F: view registry invariants ----------------------------------
#
# `navigation._VIEWS` is the single source of truth for the dashboard's
# view set. `VALID_VIEWS`, `TOP_LEVEL_VIEWS`, and `TOP_LEVEL_LABELS` are
# all derived from it; the `ViewName` Literal is kept in sync via the
# module-load assertion. These tests pin the registry contract so a
# future change can't accidentally desynchronize the derivations.


def test_view_registry_literal_and_runtime_set_agree() -> None:
    """The `ViewName` Literal and the `_VIEWS` runtime registry must
    cover the same set of view names. The module-load assertion
    already enforces this — this test makes the contract explicit
    in the test suite so a regression that loosened or removed the
    assertion still fails loudly."""
    from typing import get_args

    from tokenscope.navigation import _VIEWS, ViewName

    assert set(get_args(ViewName)) == {v.name for v in _VIEWS}


def test_valid_views_derives_from_registry_in_order() -> None:
    """`VALID_VIEWS` is the tuple of names in `_VIEWS` order (no
    sorting, no filtering). Registration order matters for the page
    selector's left-to-right rendering."""
    from tokenscope.navigation import _VIEWS, VALID_VIEWS

    assert VALID_VIEWS == tuple(v.name for v in _VIEWS)


def test_top_level_views_excludes_drill_views() -> None:
    """A view is "top-level" iff its `_ViewMeta.label` is not None.
    Drill views (day / session / block) have `label is None` and are
    reachable only via chart-click or breadcrumb — never the page
    selector."""
    from tokenscope.navigation import _VIEWS, TOP_LEVEL_VIEWS

    expected = tuple(v.name for v in _VIEWS if v.label is not None)
    assert TOP_LEVEL_VIEWS == expected

    drill_views = {v.name for v in _VIEWS if v.label is None}
    assert drill_views == {"day", "session", "block"}
    assert drill_views.isdisjoint(TOP_LEVEL_VIEWS)


def test_top_level_labels_keys_match_top_level_views() -> None:
    """`TOP_LEVEL_LABELS` is keyed by exactly the top-level view names.
    The page selector reads from this dict; an out-of-sync key set
    would raise `KeyError` at render time."""
    from tokenscope.navigation import TOP_LEVEL_LABELS, TOP_LEVEL_VIEWS

    assert set(TOP_LEVEL_LABELS) == set(TOP_LEVEL_VIEWS)


def test_app_renderer_map_covers_every_valid_view() -> None:
    """`app._RENDERERS` must have an entry for every name in
    `VALID_VIEWS` and no extras. The app's module-load assertion
    enforces this; this test makes the contract visible to the
    test suite so a regression that loosened the assertion still
    fails loudly here.

    Drift in either direction is dangerous: a missing renderer
    would `KeyError` on the first request to the new view; an
    orphan renderer is dead code that lies about what views are
    actually reachable."""
    from tokenscope.app import _RENDERERS
    from tokenscope.navigation import VALID_VIEWS

    assert set(_RENDERERS) == set(VALID_VIEWS)


def test_app_renderer_map_dispatches_each_view_to_its_module() -> None:
    """Each `ViewName` resolves to the `render` callable from the
    corresponding view module. Asserts the mapping itself (no
    Streamlit runtime needed) — the end-to-end click-and-render
    behaviour is covered by the parametrized
    `test_top_level_page_selector_click_navigates` matrix in
    `test_ui_smoke.py`."""
    from tokenscope.app import _RENDERERS
    from tokenscope.ui import (
        block as block_view,
        cache as cache_view,
        day as day_view,
        live as live_view,
        models as models_view,
        overview,
        session as session_view,
    )

    expected = {
        "overview": overview.render,
        "live": live_view.render,
        "cache": cache_view.render,
        "models": models_view.render,
        "day": day_view.render,
        "session": session_view.render,
        "block": block_view.render,
    }
    assert _RENDERERS == expected
