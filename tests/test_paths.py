"""Unit tests for tokenscope.paths.

`home_slug()` is the user's home directory encoded the way ccusage
encodes project paths (slashes → dashes, leading dash prefixed).
It's plumbed through `friendly_project_label(slug, home_slug=...)`
in two surfaces (sidebar Project dropdown, Daily view Project column)
— so the encoding rule and the cache-clear contract are both
load-bearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenscope.paths import home_slug


@pytest.fixture(autouse=True)
def _clear_home_slug_cache():
    """`home_slug` is `@lru_cache(maxsize=1)`, so values from prior
    tests persist across calls. Clear before and after each test so
    each test observes the value derived from its own `HOME` /
    `Path.home()` setup, not a stale value."""
    home_slug.cache_clear()
    yield
    home_slug.cache_clear()


def test_home_slug_encodes_path_with_leading_dash(monkeypatch) -> None:
    """`/Users/alice` → `-Users-alice`. This is the exact encoding
    ccusage uses for its project keys, so `friendly_project_label`
    can use string equality (`slug == home_slug`) to detect the
    home prefix."""
    monkeypatch.setenv("HOME", "/Users/alice")
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/alice"))
    assert home_slug() == "-Users-alice"


def test_home_slug_replaces_internal_slashes(monkeypatch) -> None:
    """Each path separator becomes a single dash — the encoding is
    lossy against dashes inside directory names (ccusage's own
    limitation), but unambiguous on the slash → dash mapping."""
    monkeypatch.setattr(Path, "home", lambda: Path("/home/bob/users"))
    assert home_slug() == "-home-bob-users"


def test_home_slug_is_cached(monkeypatch) -> None:
    """`Path.home()` is called exactly once per process; subsequent
    `home_slug()` calls return the cached value. The cache keeps the
    helper's overhead negligible per-render."""
    calls = {"n": 0}

    def fake_home() -> Path:
        calls["n"] += 1
        return Path("/Users/cached")

    monkeypatch.setattr(Path, "home", fake_home)
    a = home_slug()
    b = home_slug()
    assert a == b == "-Users-cached"
    assert calls["n"] == 1


def test_home_slug_cache_clears_on_demand(monkeypatch) -> None:
    """Tests that monkeypatch `Path.home()` must be able to observe
    the new value — `home_slug.cache_clear()` is the documented
    escape hatch. Regression: a regression that removes
    `@lru_cache` OR makes the cache scope different would break the
    autouse fixture above, and this test pins the contract."""
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/first"))
    assert home_slug() == "-Users-first"
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/second"))
    home_slug.cache_clear()
    assert home_slug() == "-Users-second"
