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
    new = nav.to_session("new")
    assert new == Navigation(view="session", day="2026-05-16", session="new")


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

    assert TOP_LEVEL_VIEWS == ("overview", "cache", "models")
