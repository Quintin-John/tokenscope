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
    """ccusage.py exposes only the two uncached wrappers used by the
    live integration tests plus `get_ccusage_version`. The
    weekly/monthly/session/*_by_project variants live in
    `tokenscope.data` (cached) — having two parallel surfaces was
    dead duplication and was removed. Re-adding any of them is
    a 3-line function when an integration test actually needs it.
    """
    from tokenscope import ccusage

    public = {"daily", "blocks", "get_ccusage_version"}
    for name in public:
        assert callable(getattr(ccusage, name)), name

    # Negative assertion: the dead duplicates must not creep back.
    for removed in (
        "weekly",
        "monthly",
        "session",
        "daily_by_project",
        "weekly_by_project",
        "monthly_by_project",
    ):
        assert not hasattr(ccusage, removed), (
            f"ccusage.{removed} was removed as dead duplication of "
            f"data.{removed}; do not re-add without a concrete caller."
        )


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
        "top_n_by_cost",
        "model_family",
        "mtd_cost",
        "today_cost",
        "aggregate_cache_hit_ratio",
        "active_block_burn",
        "densify_daily_costs",
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
        "window_cost",
        "last_day_cost",
        "model_breakdown",
        "short_model_label",
        "prior_window_query",
        "window_effective_per_mtok",
        "typical_burn_rate",
        "blocks_for_session",
        "cost_by_kind",
        "block_cache_hit_ratio",
        "block_cost_by_kind",
        "cache_savings",
        "daily_cache_savings",
        "per_model_cache_performance",
        "cache_data_range",
        "cost_concentration_summary",
        "DailyCell",
        "DailySummary",
        "WindowTotals",
        "daily_cells",
        "daily_summaries",
        "window_totals",
        "filter_daily_by_project_models",
        "available_models_by_project",
        "cells_for_date",
        "daily_project_aggregates",
        "pluralize",
        "peak_day",
        "active_days_count",
        "avg_cost_per_active_day",
        "busiest_model",
        "display_model_label",
    ):
        assert callable(getattr(analytics, name)), name


def test_paths_public_surface() -> None:
    """`tokenscope.paths` exposes the project-display helpers used
    by both Daily (table Project column) and Sidebar (Project
    dropdown). One module hosts the rule; one drift guard pins it.

    `friendly_project_label` lives here after migrating out of
    `analytics.py` — analytics was the wrong home for a slug-string
    helper. `resolve_project_slug` + `project_display_name` are
    Slice-8 additions: the filesystem-backed authoritative rule.
    """
    from tokenscope import paths

    for name in (
        "home_slug",
        "friendly_project_label",
        "resolve_project_slug",
        "project_display_name",
    ):
        assert callable(getattr(paths, name)), name


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


# ---------- type-annotation resolution (slice 7) ----------
#
# `from __future__ import annotations` stores annotations as strings.
# A typo in a type name wouldn't fail at module load — it would just
# resolve to nothing during runtime introspection. We rely on these
# annotations being correct (mypy is NOT installed as a dev dep, so
# we can't fall back on a type-checker), so the test resolves them
# explicitly via `typing.get_type_hints` and asserts identity. If
# someone later mistypes one, this fails immediately.


def _resolved_hint(func, param_name: str):
    """Return the resolved (class, not string) annotation for `param_name`
    on `func`. `include_extras=False` strips Annotated[] wrappers we
    don't currently use."""
    from typing import get_type_hints

    hints = get_type_hints(func, include_extras=False)
    return hints[param_name]


def test_overview_render_kpis_param_types_resolve() -> None:
    """`_render_kpis` carries `DailyReport` on its first param. The
    Active-block burn KPI was retired (Live owns active-block data
    now), so `BlocksReport` is no longer a parameter — this test was
    updated to reflect the post-Overview-rework signature."""
    from tokenscope.models import DailyReport
    from tokenscope.ui.overview import _render_kpis

    assert _resolved_hint(_render_kpis, "daily_report") is DailyReport


def test_overview_render_cost_composition_param_type_resolves() -> None:
    from tokenscope.models import DailyReport
    from tokenscope.ui.overview import _render_cost_composition

    assert _resolved_hint(_render_cost_composition, "daily_report") is DailyReport


def test_models_view_render_token_kind_composition_param_type_resolves() -> None:
    """The Models view's chart-card helper carries an annotated
    `daily_report` so static type checks catch any future
    misroute to a non-DailyReport caller."""
    from tokenscope.models import DailyReport
    from tokenscope.ui.models import _render_token_kind_composition

    assert (
        _resolved_hint(_render_token_kind_composition, "daily_report")
        is DailyReport
    )


def test_day_view_row_entity_param_types_resolve() -> None:
    """Audit Notable #8: _session_row and _block_row added types in
    slice 5. Pin them here so the type-hint sweep covers the whole
    audit finding in one regression net."""
    from tokenscope.models import BlockEntry, SessionEntry
    from tokenscope.ui.day import _block_row, _session_row

    assert _resolved_hint(_session_row, "session") is SessionEntry
    assert _resolved_hint(_block_row, "block") is BlockEntry


def test_no_stale_live_token_throughput_references() -> None:
    """`live_token_throughput` was replaced by
    `live_token_kind_composition_bar`. The deleted symbol must not appear
    anywhere in the package source — comments/docstrings naming a function
    a reader can't find are misleading. Negative guard so it can't creep
    back (mirrors the dead-duplicate guard in test_ccusage_public_surface)."""
    from pathlib import Path

    import tokenscope

    root = Path(tokenscope.__file__).parent
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "live_token_throughput" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"stale `live_token_throughput` references remain in: {offenders}"
    )
