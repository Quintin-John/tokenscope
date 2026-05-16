"""Smoke tests: package imports cleanly, public surface exists."""

from __future__ import annotations


def test_package_imports() -> None:
    import tokenscope

    assert tokenscope.__version__


def test_submodules_import() -> None:
    from tokenscope import app, ccusage, models  # noqa: F401


def test_app_public_surface() -> None:
    from tokenscope import app

    assert callable(app.render)
    assert callable(app.main)


def test_ccusage_public_surface() -> None:
    from tokenscope import ccusage

    for name in ("daily", "weekly", "monthly", "session", "blocks", "get_ccusage_version"):
        assert callable(getattr(ccusage, name)), name


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
