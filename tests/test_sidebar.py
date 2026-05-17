"""Unit tests for tokenscope.ui.sidebar.

The sidebar's full render flow is exercised via AppTest in
`tests/test_ui_smoke.py`. This file holds focused tests on the
pure-Python helpers that don't touch Streamlit's widget runtime —
date-preset range builders, the preset registry, and the CSS
resource.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path
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


# ---------- date-range preset helpers ----------


def test_last_n_days_range_is_inclusive_of_today() -> None:
    """`7d` conventionally means seven days *including* today. The
    off-by-one (`days=n-1`) belongs in the helper, not at call sites."""
    today = _date(2026, 5, 17)
    since, until = sidebar._last_n_days_range(today, 7)
    assert since == _date(2026, 5, 11)
    assert until == today
    assert (until - since).days + 1 == 7


def test_last_n_days_range_thirty_days() -> None:
    today = _date(2026, 5, 17)
    since, until = sidebar._last_n_days_range(today, 30)
    assert since == _date(2026, 4, 18)
    assert (until - since).days + 1 == 30


def test_last_n_days_range_single_day_collapses() -> None:
    """n=1 collapses to (today, today)."""
    today = _date(2026, 5, 17)
    since, until = sidebar._last_n_days_range(today, 1)
    assert since == until == today


def test_month_to_date_range_starts_on_day_one() -> None:
    since, until = sidebar._month_to_date_range(_date(2026, 5, 17))
    assert since == _date(2026, 5, 1)
    assert until == _date(2026, 5, 17)


def test_month_to_date_range_on_first_of_month() -> None:
    since, until = sidebar._month_to_date_range(_date(2026, 5, 1))
    assert since == until == _date(2026, 5, 1)


def test_custom_range_marker_returns_none() -> None:
    """The `Custom` preset is the passive choice — its builder returns
    None so the dispatch handler can leave the date range untouched."""
    assert sidebar._custom_range_marker(_date(2026, 5, 17)) is None


def test_date_presets_registry_covers_expected_labels() -> None:
    """The registry is the single source of truth for the preset row.
    Adding or renaming a preset is one tuple entry; the smoke tests
    that assert on labels would fail loudly if the contract drifted."""
    labels = [p.label for p in sidebar._DATE_PRESETS]
    assert labels == ["7d", "30d", "MTD", "Custom"]


def test_date_presets_builders_produce_consistent_shapes() -> None:
    """Every preset's builder accepts `today` and returns either a
    `(since, until)` tuple or `None` (Custom). The dispatch handler
    relies on this binary."""
    today = _date(2026, 5, 17)
    for preset in sidebar._DATE_PRESETS:
        result = preset.builder(today)
        if preset.label == "Custom":
            assert result is None
        else:
            assert isinstance(result, tuple) and len(result) == 2
            since, until = result
            assert isinstance(since, _date) and isinstance(until, _date)
            assert since <= until <= today


# ---------- _resolve_date_preset (pure dispatch) ----------


def test_resolve_date_preset_none_label_returns_none() -> None:
    """Segmented control returns `None` until the user clicks — the
    callback fires with no label, the resolver returns None, the
    handler no-ops."""
    today = _date(2026, 5, 17)
    assert sidebar._resolve_date_preset(None, today) is None
    assert sidebar._resolve_date_preset("", today) is None


def test_resolve_date_preset_unknown_label_returns_none() -> None:
    """Defensive — a forged URL or stale session_state value can't
    crash the handler."""
    today = _date(2026, 5, 17)
    assert sidebar._resolve_date_preset("99d", today) is None


def test_resolve_date_preset_active_preset_returns_range() -> None:
    today = _date(2026, 5, 17)
    result = sidebar._resolve_date_preset("7d", today)
    assert result is not None
    since, until = result
    assert (until - since).days + 1 == 7
    assert until == today


def test_resolve_date_preset_custom_returns_none() -> None:
    """Custom is the passive sentinel — the resolver returns None so
    the handler leaves the date range untouched."""
    today = _date(2026, 5, 17)
    assert sidebar._resolve_date_preset("Custom", today) is None


# ---------- CSS resource ----------


def test_sidebar_css_resource_is_loaded() -> None:
    """The sibling `.css` file is read at import time. If the file
    drifts out of the package or the read fails silently, the
    constant would still be a string — verify it's a real CSS
    document by checking for known selectors."""
    css = sidebar._SIDEBAR_CSS
    assert isinstance(css, str) and css.strip()
    # Section heading rule (the visual-hierarchy fix).
    assert "[data-testid=\"stSidebar\"] h3" in css
    # Chip color override (the red-chip fix).
    assert "[data-baseweb=\"tag\"]" in css
    # Backtick / code-pill suppression (defensive against regressions).
    assert "[data-testid=\"stSidebar\"] code" in css


def test_sidebar_css_file_lives_next_to_module() -> None:
    """The CSS file must live in the same directory as sidebar.py so
    it ships in the wheel build (Hatch includes everything under
    `packages = ["src/tokenscope"]`)."""
    css_path = Path(sidebar.__file__).parent / "_sidebar_styles.css"
    assert css_path.is_file()
