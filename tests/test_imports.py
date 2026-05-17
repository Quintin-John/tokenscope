"""Smoke tests: package imports cleanly, public surface exists."""

from __future__ import annotations


def test_package_imports() -> None:
    import tokenscope

    assert tokenscope.__version__


def test_submodules_import() -> None:
    from tokenscope import (  # noqa: F401
        analytics,
        app,
        ccusage,
        data,
        models,
        navigation,
        plans,
        pricing,
        query,
    )
    from tokenscope.ui import (  # noqa: F401
        block,
        breadcrumbs,
        cache,
        charts,
        day,
        live,
        models as models_ui,
        overview,
        session,
        sidebar,
    )


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

    q = Query(since="20260401", until="20260501", project="demo", offline=True)
    assert q.to_args() == [
        "--since=20260401",
        "--until=20260501",
        "--project=demo",
        "--offline",
    ]
    # Frozen → hashable so @st.cache_data can key on it.
    assert hash(q) == hash(
        Query(since="20260401", until="20260501", project="demo", offline=True)
    )


def test_query_project_with_leading_dash() -> None:
    """Regression: ccusage's parser treats a space-separated project value
    starting with `-` as the next flag, so we emit --project=<value>."""
    from tokenscope.query import Query

    project_id = "-Users-quintin-johnsmith-Documents-JavaCode-IT-Artifact"
    args = Query(project=project_id).to_args()
    # The project must be a single argv entry so ccusage's parser can't
    # mistake it for another flag.
    assert args == [f"--project={project_id}"]
    assert all(not (a.startswith("-") and " " in a) for a in args)


def test_query_offline_default_false() -> None:
    from tokenscope.query import Query

    assert Query().to_args() == []
    assert Query(offline=False).to_args() == []


def test_analytics_public_surface() -> None:
    from tokenscope import analytics

    for name in (
        "rolling_cost_average",
        "cache_hit_ratio",
        "dollars_saved",
        "top_n_by_cost",
        "model_family",
        "mtd_cost",
        "today_cost",
        "aggregate_cache_hit_ratio",
        "active_block_burn",
        "daily_cost_by_model",
        "daily_token_mix",
        "find_daily_entry",
        "sessions_on_day",
        "blocks_on_day",
        "find_session",
        "find_block",
        "cost_share_by_model",
        "filter_daily_by_models",
        "available_models",
        "daily_cache_hit_ratio",
        "daily_dollars_saved",
        "token_flow_sankey_data",
        "window_cost",
        "last_day_cost",
        "model_breakdown",
        "friendly_project_label",
        "short_model_label",
    ):
        assert callable(getattr(analytics, name)), name


def test_navigation_public_surface() -> None:
    from tokenscope.navigation import Navigation

    nav = Navigation()
    assert nav.view == "overview"
    assert Navigation.from_params({"view": "day", "day": "2026-05-16"}).day == "2026-05-16"


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
