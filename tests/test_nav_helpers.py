"""Direct unit tests for the shared navigation helpers (slice — DRY pass).

These exercise `tokenscope.ui._nav.route_to` and `.handle_chart_drill`
without booting the Streamlit runtime, by monkeypatching the bits of
`streamlit` that the helpers reach for. Faster than the AppTest path
and pinpoints regressions to the helper functions themselves.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tokenscope.navigation import Navigation
from tokenscope.ui import _nav


@pytest.fixture
def fake_streamlit(monkeypatch):
    """Capture writes to `st.query_params` and `st.rerun` so we can
    inspect what `route_to` actually wrote."""
    state: dict[str, object] = {"params": {}, "rerun_called": 0}

    class FakeParams:
        def clear(self) -> None:
            state["params"] = {}

        def __setitem__(self, key, value):
            cast = dict(state["params"])  # type: ignore[arg-type]
            cast[key] = value
            state["params"] = cast

        def __delitem__(self, key):
            cast = dict(state["params"])  # type: ignore[arg-type]
            cast.pop(key, None)
            state["params"] = cast

        def __contains__(self, key):
            return key in state["params"]  # type: ignore[operator]

    def fake_rerun():
        state["rerun_called"] = int(state["rerun_called"]) + 1  # type: ignore[arg-type]

    monkeypatch.setattr(_nav.st, "query_params", FakeParams())
    monkeypatch.setattr(_nav.st, "rerun", fake_rerun)
    return state


# ---------- route_to ----------


def test_route_to_writes_target_params_and_reruns(fake_streamlit) -> None:
    _nav.route_to(Navigation(view="day", day="2026-05-16"))
    assert fake_streamlit["params"] == {"view": "day", "day": "2026-05-16"}
    assert fake_streamlit["rerun_called"] == 1


def test_route_to_clears_stale_params(fake_streamlit) -> None:
    """A leftover `block=` from a previous nav shouldn't survive the route."""
    fake_streamlit["params"] = {"view": "block", "day": "x", "session": "s", "block": "b"}
    _nav.route_to(Navigation(view="overview"))
    assert fake_streamlit["params"] == {"view": "overview"}


def test_route_to_writes_extra_params(fake_streamlit) -> None:
    """Models view drill passes `models=` alongside the Navigation."""
    _nav.route_to(
        Navigation(view="overview"),
        extra_params={"models": "claude-opus-4-7"},
    )
    assert fake_streamlit["params"] == {
        "view": "overview",
        "models": "claude-opus-4-7",
    }


# ---------- handle_chart_drill ----------


def test_handle_chart_drill_no_event_is_noop(fake_streamlit) -> None:
    _nav.handle_chart_drill(None, Navigation.to_day, chart_key="test-chart")
    assert fake_streamlit["rerun_called"] == 0


def test_handle_chart_drill_empty_selection_is_noop(fake_streamlit) -> None:
    event = SimpleNamespace(selection=SimpleNamespace(points=[]))
    _nav.handle_chart_drill(event, Navigation.to_day, chart_key="test-chart")
    assert fake_streamlit["rerun_called"] == 0


def test_handle_chart_drill_routes_on_point_click(fake_streamlit) -> None:
    """Day chart: point's `x` is a date, factory wraps it into Navigation."""
    event = SimpleNamespace(
        selection=SimpleNamespace(points=[{"x": "2026-05-16T00:00:00"}])
    )
    nav = Navigation(view="overview")
    _nav.handle_chart_drill(event, lambda x: nav.to_day(x[:10]), chart_key="test-chart")
    assert fake_streamlit["params"] == {"view": "day", "day": "2026-05-16"}
    assert fake_streamlit["rerun_called"] == 1


def test_handle_chart_drill_uses_y_when_no_x(fake_streamlit) -> None:
    """px.timeline puts the block id on `y` (horizontal bars)."""
    event = SimpleNamespace(
        selection=SimpleNamespace(points=[{"y": "2026-05-16T13:00:00.000Z"}])
    )
    nav = Navigation(view="session", session="sess-a")
    _nav.handle_chart_drill(event, nav.to_block, chart_key="test-chart")
    # Block id passed through verbatim (no truncation).
    assert fake_streamlit["params"]["block"] == "2026-05-16T13:00:00.000Z"


def test_handle_chart_drill_falls_back_to_label(fake_streamlit) -> None:
    event = SimpleNamespace(
        selection=SimpleNamespace(points=[{"label": "2026-05-16"}])
    )
    nav = Navigation(view="overview")
    _nav.handle_chart_drill(event, lambda x: nav.to_day(x[:10]), chart_key="test-chart")
    assert fake_streamlit["params"] == {"view": "day", "day": "2026-05-16"}


def test_handle_chart_drill_missing_keys_is_noop(fake_streamlit) -> None:
    event = SimpleNamespace(selection=SimpleNamespace(points=[{"foo": "bar"}]))
    _nav.handle_chart_drill(event, Navigation.to_day, chart_key="test-chart")
    assert fake_streamlit["rerun_called"] == 0
