"""Smoke tests: package imports cleanly, public surface exists."""

from __future__ import annotations


def test_package_imports() -> None:
    import tokenscope

    assert tokenscope.__version__


def test_submodules_import() -> None:
    from tokenscope import analytics, app, ccusage, data, models, query  # noqa: F401


def test_app_public_surface() -> None:
    from tokenscope import app

    assert callable(app.render)
    assert callable(app.main)


def test_ccusage_public_surface() -> None:
    from tokenscope import ccusage

    for name in (
        "daily",
        "weekly",
        "monthly",
        "session",
        "blocks",
        "daily_by_project",
        "weekly_by_project",
        "monthly_by_project",
        "get_ccusage_version",
    ):
        assert callable(getattr(ccusage, name)), name


def test_data_layer_public_surface() -> None:
    from tokenscope import data

    for name in (
        "daily",
        "weekly",
        "monthly",
        "session",
        "blocks",
        "daily_by_project",
        "weekly_by_project",
        "monthly_by_project",
    ):
        assert callable(getattr(data, name)), name


def test_query_public_surface() -> None:
    from tokenscope.query import Query

    q = Query(since="20260401", until="20260501", project="demo")
    assert q.to_args() == ["--since", "20260401", "--until", "20260501", "--project", "demo"]
    # Frozen → hashable so @st.cache_data can key on it.
    assert hash(q) == hash(Query(since="20260401", until="20260501", project="demo"))


def test_analytics_public_surface() -> None:
    from tokenscope import analytics

    for name in (
        "rolling_cost_average",
        "cache_hit_ratio",
        "dollars_saved",
        "top_n_by_cost",
        "model_family",
    ):
        assert callable(getattr(analytics, name)), name


def test_models_public_surface() -> None:
    from tokenscope.models import (
        BlocksReport,
        DailyReport,
        MonthlyReport,
        SessionReport,
        WeeklyReport,
    )

    for cls in (DailyReport, WeeklyReport, MonthlyReport, SessionReport, BlocksReport):
        assert hasattr(cls, "model_validate")
