"""Live ccusage integration tests.

These actually shell out to the locally-installed ccusage. Opt-in only:

    uv run pytest -m integration

They are skipped by default (see pyproject.toml `addopts = -m 'not integration'`).
"""

from __future__ import annotations

import pytest

from tokenscope import ccusage


pytestmark = pytest.mark.integration


def test_version_resolves() -> None:
    v = ccusage.get_ccusage_version()
    assert v
    assert isinstance(v, str)


def test_daily_runs() -> None:
    report = ccusage.daily()
    assert report.daily is not None


def test_blocks_active_runs() -> None:
    report = ccusage.blocks(active=True)
    assert report.blocks is not None
