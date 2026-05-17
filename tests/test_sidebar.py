"""Unit tests for tokenscope.ui.sidebar.

The sidebar's full render flow is exercised via AppTest in
`tests/test_ui_smoke.py`. This file holds focused tests on the
section renderers extracted in slice 6 — the pure-Python helpers
that don't touch Streamlit's widget runtime.

`_fetch_discovery_options` is the only helper with non-trivial
behaviour (silent-swallow of CcusageError); the rest are
Streamlit-widget wrappers covered by the smoke tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from tokenscope import data
from tokenscope.ccusage import CcusageError
from tokenscope.query import Query
from tokenscope.ui import sidebar


def _q() -> Query:
    return Query(since="20260401", until="20260516", offline=False)


# ---------- _fetch_discovery_options ----------


def test_fetch_discovery_options_returns_models_and_projects(monkeypatch) -> None:
    """Happy path: both data.* calls succeed and the helper returns
    (sorted_models, sorted_projects)."""

    class _FakeReport:
        def __init__(self, *, models: list[str] | None = None, projects: dict[str, Any] | None = None):
            self._models = models
            self.projects = projects or {}
            self.daily = [type("E", (), {"models_used": models or []})()]

    def fake_daily(_q: Query | None = None):
        # Two distinct models in the synthetic daily entry — the helper
        # delegates the actual extraction to analytics.available_models,
        # which we exercise via the real call.
        return _FakeReport(models=["claude-opus-4-7", "claude-haiku-4-5-20251001"])

    def fake_daily_by_project(_q: Query | None = None):
        return _FakeReport(projects={
            "-Users-foo-proj-a": object(),
            "-Users-foo-proj-b": object(),
        })

    monkeypatch.setattr(data, "daily", fake_daily)
    monkeypatch.setattr(data, "daily_by_project", fake_daily_by_project)

    models, projects = sidebar._fetch_discovery_options(_q())

    # available_models sorts ascending; same for project keys.
    assert models == ["claude-haiku-4-5-20251001", "claude-opus-4-7"]
    assert projects == ["-Users-foo-proj-a", "-Users-foo-proj-b"]


def test_fetch_discovery_options_swallows_daily_error(monkeypatch) -> None:
    """If the daily call raises CcusageError, the helper returns empty
    model_options but still attempts the by_project call. This is the
    "first-run user without ccusage" path — the sidebar must render
    something, not crash."""

    def fake_daily(_q: Query | None = None):
        raise CcusageError("simulated ccusage failure")

    class _ProjReport:
        projects = {"-Users-foo-proj-a": object()}

    monkeypatch.setattr(data, "daily", fake_daily)
    monkeypatch.setattr(data, "daily_by_project", lambda _q=None: _ProjReport())

    models, projects = sidebar._fetch_discovery_options(_q())
    assert models == []
    assert projects == ["-Users-foo-proj-a"]


def test_fetch_discovery_options_swallows_by_project_error(monkeypatch) -> None:
    """Symmetric to the previous test — by_project failing must not
    prevent the models list from being populated."""

    class _DailyReport:
        daily = [type("E", (), {"models_used": ["claude-opus-4-7"]})()]

    def fake_by_project(_q: Query | None = None):
        raise CcusageError("simulated ccusage failure")

    monkeypatch.setattr(data, "daily", lambda _q=None: _DailyReport())
    monkeypatch.setattr(data, "daily_by_project", fake_by_project)

    models, projects = sidebar._fetch_discovery_options(_q())
    assert models == ["claude-opus-4-7"]
    assert projects == []


def test_fetch_discovery_options_swallows_both_errors(monkeypatch) -> None:
    """Both calls fail → both lists empty, no exception escapes.

    The user sees empty Project / Models dropdowns, the main view
    surfaces the underlying ccusage error via its own `st.error` path
    on the next data.daily call. The sidebar itself must not crash.
    """
    def _raise(_q: Query | None = None):
        raise CcusageError("simulated ccusage failure")

    monkeypatch.setattr(data, "daily", _raise)
    monkeypatch.setattr(data, "daily_by_project", _raise)

    models, projects = sidebar._fetch_discovery_options(_q())
    assert models == []
    assert projects == []


def test_fetch_discovery_options_does_not_swallow_unrelated_errors(monkeypatch) -> None:
    """The except clauses catch CcusageError specifically, not
    Exception. A real bug (TypeError, ValueError, etc.) must surface
    rather than being silently swallowed."""

    def _raise_runtime(_q: Query | None = None):
        raise RuntimeError("not a ccusage failure — a programmer error")

    monkeypatch.setattr(data, "daily", _raise_runtime)

    with pytest.raises(RuntimeError, match="programmer error"):
        sidebar._fetch_discovery_options(_q())
