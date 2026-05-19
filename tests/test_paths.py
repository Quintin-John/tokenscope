"""Unit tests for tokenscope.paths.

The module hosts four public helpers:

  - `home_slug()`             — current user's home dir slugified
  - `friendly_project_label()` — slug → home-relative display string
  - `resolve_project_slug()`  — slug → real Path via filesystem walk
  - `project_display_name()`  — authoritative display rule (resolves
                                 to basename when possible, falls
                                 back to friendly_project_label)

The encoding rule, cache-clear contract, fallback path, and greedy
walk algorithm are all load-bearing. Tests pin each.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenscope.paths import (
    friendly_project_label,
    home_slug,
    project_display_name,
    resolve_project_slug,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Both `home_slug` and `resolve_project_slug` are `@lru_cache`-
    decorated. Clear before AND after each test so monkeypatched
    filesystem state never leaks between scenarios."""
    home_slug.cache_clear()
    resolve_project_slug.cache_clear()
    yield
    home_slug.cache_clear()
    resolve_project_slug.cache_clear()


# ---------- home_slug ----------


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
    `home_slug()` calls return the cached value."""
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
    escape hatch."""
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/first"))
    assert home_slug() == "-Users-first"
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/second"))
    home_slug.cache_clear()
    assert home_slug() == "-Users-second"


# ---------- friendly_project_label (moved from analytics) ----------

_HOME_SLUG_FOR_LABEL = "-Users-quintin-johnsmith"


def test_friendly_project_label_home_dir_itself() -> None:
    """Regression for the johnsmith bug: the home directory slug must
    NOT be rendered as 'johnsmith — Users/quintin'."""
    assert (
        friendly_project_label(_HOME_SLUG_FOR_LABEL, home_slug=_HOME_SLUG_FOR_LABEL)
        == "~"
    )


def test_friendly_project_label_under_home() -> None:
    assert (
        friendly_project_label(
            "-Users-quintin-johnsmith-Documents-RiderProjects-WorldForge",
            home_slug=_HOME_SLUG_FOR_LABEL,
        )
        == "~/Documents-RiderProjects-WorldForge"
    )


def test_friendly_project_label_under_home_hyphenated_dir() -> None:
    """Hyphenated directory name (mini-ollama-ui) survives verbatim —
    we can't recover its structure from the slug but we shouldn't
    mangle it."""
    assert (
        friendly_project_label(
            "-Users-quintin-johnsmith-Downloads-mini-ollama-ui",
            home_slug=_HOME_SLUG_FOR_LABEL,
        )
        == "~/Downloads-mini-ollama-ui"
    )


def test_friendly_project_label_outside_home() -> None:
    """Path not under home → strip leading dash, leave the rest verbatim."""
    assert (
        friendly_project_label(
            "-Volumes-SSK-Drive--ManageLiterature", home_slug=_HOME_SLUG_FOR_LABEL
        )
        == "Volumes-SSK-Drive--ManageLiterature"
    )


def test_friendly_project_label_no_home_slug() -> None:
    """Without home info, we just strip the leading dash."""
    assert (
        friendly_project_label("-Users-anyone-Documents-Foo")
        == "Users-anyone-Documents-Foo"
    )


def test_friendly_project_label_passthrough() -> None:
    assert friendly_project_label("Unknown Project") == "Unknown Project"
    assert friendly_project_label("") == ""


def test_friendly_project_label_home_lookalike() -> None:
    """A different user's home should not match — we require exact prefix."""
    result = friendly_project_label(
        "-Users-jane-Documents-Hack", home_slug=_HOME_SLUG_FOR_LABEL
    )
    # No home match → fall back to leading-dash strip.
    assert result == "Users-jane-Documents-Hack"


# ---------- resolve_project_slug ----------


def test_resolve_project_slug_returns_none_for_empty() -> None:
    assert resolve_project_slug("") is None


def test_resolve_project_slug_returns_none_without_leading_dash() -> None:
    """A bare-name slug (`tokenscope`, no leading dash) isn't a
    ccusage-encoded path — bail out rather than guess."""
    assert resolve_project_slug("tokenscope") is None


def test_resolve_project_slug_returns_none_when_no_path_exists(monkeypatch) -> None:
    """No decoding of the slug lands on a real directory → None."""
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    assert resolve_project_slug("-Users-nope-nothere") is None


def test_resolve_project_slug_walks_simple_path(tmp_path) -> None:
    """Build a real directory tree under tmp_path and walk into it.
    The greedy algorithm finds `/<tmp>/Users/q/proj` for the slug
    `-Users-q-proj` (when rooted at tmp_path)."""
    real = tmp_path / "Users" / "q" / "proj"
    real.mkdir(parents=True)
    fake_root_slug = "-" + str(tmp_path).lstrip("/").replace("/", "-")
    slug = fake_root_slug + "-Users-q-proj"
    resolved = resolve_project_slug(slug)
    assert resolved == real


def test_resolve_project_slug_disambiguates_hyphenated_dir(tmp_path) -> None:
    """Two possible decodings: `tmp/Users/q/repo-with-dash` (one
    dir) or `tmp/Users/q/repo/with/dash` (nested). Only the first
    exists on disk; the greedy walk must pick it via the longest-
    match rule."""
    real = tmp_path / "Users" / "q" / "repo-with-dash"
    real.mkdir(parents=True)
    fake_root_slug = "-" + str(tmp_path).lstrip("/").replace("/", "-")
    slug = fake_root_slug + "-Users-q-repo-with-dash"
    resolved = resolve_project_slug(slug)
    assert resolved == real
    # Sanity: `.name` is the full hyphenated dir, not a fragment.
    assert resolved.name == "repo-with-dash"


def test_resolve_project_slug_returns_home_path_when_slug_is_home(monkeypatch) -> None:
    """The slug `-Users-q-johnsmith` should resolve to `/Users/q-johnsmith`
    when that directory exists. project_display_name then renders this as `~`."""
    # Real filesystem under macOS: /Users/quintin-johnsmith exists.
    # Use Path.home() to make this portable.
    home = Path.home()
    if not home.is_dir():
        pytest.skip("Path.home() doesn't resolve to a real directory")
    home_str = str(home).lstrip("/")
    slug = "-" + home_str.replace("/", "-")
    assert resolve_project_slug(slug) == home


def test_resolve_project_slug_cached(monkeypatch, tmp_path) -> None:
    """Cache hit on the second call — `Path.is_dir` should be invoked
    only on the first resolution of a given slug."""
    real = tmp_path / "Users" / "q" / "proj"
    real.mkdir(parents=True)
    fake_root_slug = "-" + str(tmp_path).lstrip("/").replace("/", "-")
    slug = fake_root_slug + "-Users-q-proj"

    call_count = {"n": 0}
    original_is_dir = Path.is_dir

    def counting_is_dir(self) -> bool:
        call_count["n"] += 1
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", counting_is_dir)
    resolve_project_slug.cache_clear()
    first = resolve_project_slug(slug)
    calls_after_first = call_count["n"]
    second = resolve_project_slug(slug)
    calls_after_second = call_count["n"]
    assert first == second
    assert calls_after_first > 0, "first call must walk the filesystem"
    assert calls_after_second == calls_after_first, (
        "second call must hit the cache and not re-walk"
    )


# ---------- project_display_name ----------


def test_project_display_name_basename_for_resolved_path(tmp_path) -> None:
    """When the slug resolves to a real path, render its basename."""
    real = tmp_path / "Users" / "q" / "tokenscope"
    real.mkdir(parents=True)
    fake_root_slug = "-" + str(tmp_path).lstrip("/").replace("/", "-")
    slug = fake_root_slug + "-Users-q-tokenscope"
    assert project_display_name(slug) == "tokenscope"


def test_project_display_name_tilde_for_home() -> None:
    """When the slug resolves to exactly Path.home(), render `~` —
    not the basename (which would be `q-johnsmith` or similar)."""
    home = Path.home()
    if not home.is_dir():
        pytest.skip("Path.home() doesn't resolve to a real directory")
    home_str = str(home).lstrip("/")
    slug = "-" + home_str.replace("/", "-")
    assert project_display_name(slug) == "~"


def test_project_display_name_falls_back_to_friendly_label(monkeypatch) -> None:
    """When `resolve_project_slug` returns None (path missing, drive
    unmounted, etc.), fall back to `friendly_project_label` so the
    column still renders something readable."""
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/quintin-johnsmith"))
    home_slug.cache_clear()
    # No decoding of this slug exists on disk → fallback engages.
    result = project_display_name(
        "-Users-quintin-johnsmith-baremetal-audit"
    )
    # Fallback returns the friendly-label form (`~/baremetal-audit`).
    assert result == "~/baremetal-audit"


def test_project_display_name_empty_passthrough() -> None:
    """Empty slug passes through unchanged — no traceback, no
    synthetic placeholder."""
    assert project_display_name("") == ""
