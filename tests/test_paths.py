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

import json
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
#
# `resolve_project_slug` reads the authoritative cwd from JSONL
# transcript files Claude Code writes under
# `~/.claude/projects/<slug>/*.jsonl`. The directory tree is
# mounted into the Docker container even though the cwd-encoded
# host paths inside the slug aren't, which is why the JSONL path
# (not a filesystem walk) is the right oracle.
#
# Tests build a fake `~/.claude/projects/<slug>/` directory under
# `tmp_path` and monkeypatch `Path.home()` to that tmp_path. That
# gives each test an isolated filesystem with the exact JSONL
# content it needs.


def _write_session_jsonl(
    project_dir: Path, cwd: str, *, filename: str = "session.jsonl"
) -> Path:
    """Build a minimal session JSONL with a single record carrying
    `cwd`. Mirrors the real Claude Code format: each line is a
    standalone JSON object; substantive records carry `cwd`."""
    project_dir.mkdir(parents=True, exist_ok=True)
    jsonl = project_dir / filename
    jsonl.write_text(
        json.dumps({"type": "user", "cwd": cwd, "sessionId": "test-session"})
        + "\n"
    )
    return jsonl


def test_resolve_project_slug_returns_none_for_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_project_slug("") is None


def test_resolve_project_slug_returns_none_when_project_dir_missing(
    monkeypatch, tmp_path
) -> None:
    """No `~/.claude/projects/<slug>/` directory → None. The slug
    is unknown locally; fallback kicks in at the caller."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_project_slug("-this-slug-has-no-transcripts") is None


def test_resolve_project_slug_returns_none_when_no_jsonl_has_cwd(
    monkeypatch, tmp_path
) -> None:
    """Project directory exists but no JSONL line yields a cwd
    field. Possible if every record is session metadata (e.g.
    older Claude Code versions). Returns None defensively."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-proj"
    project_dir = tmp_path / ".claude" / "projects" / slug
    project_dir.mkdir(parents=True)
    # JSONL with records that lack a `cwd` field.
    (project_dir / "session.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": "x"}) + "\n"
        + json.dumps({"type": "snapshot"}) + "\n"
    )
    assert resolve_project_slug(slug) is None


def test_resolve_project_slug_reads_cwd_from_jsonl(monkeypatch, tmp_path) -> None:
    """The first JSONL record with a `cwd` field is the authoritative
    source. `resolve_project_slug` returns `Path(cwd)` verbatim —
    the slug→cwd transform is delegated to Claude Code's own
    transcript metadata, not reverse-engineered from the slug."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-Documents-RiderProjects-tokenscope"
    cwd = "/Users/test/Documents/RiderProjects/tokenscope"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug, cwd=cwd
    )
    assert resolve_project_slug(slug) == Path(cwd)


def test_resolve_project_slug_works_with_hyphenated_dir_names(
    monkeypatch, tmp_path
) -> None:
    """The JSONL cwd preserves the original directory name verbatim,
    so a repo named `baremetal-audit` (with a literal hyphen)
    resolves to `/Users/test/baremetal-audit` — the lossy
    `-`-encoding ambiguity is bypassed entirely by reading the
    cwd directly."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-baremetal-audit"
    cwd = "/Users/test/baremetal-audit"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug, cwd=cwd
    )
    assert resolve_project_slug(slug) == Path(cwd)
    assert resolve_project_slug(slug).name == "baremetal-audit"


def test_resolve_project_slug_skips_records_without_cwd(
    monkeypatch, tmp_path
) -> None:
    """Real Claude Code JSONLs lead with session-metadata records
    (no `cwd`) before substantive records. The function must skip
    cwd-less lines and find the first one that has it."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-proj"
    project_dir = tmp_path / ".claude" / "projects" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "session.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": "abc"}) + "\n"
        + json.dumps({"type": "snapshot"}) + "\n"
        + json.dumps({"type": "user", "cwd": "/Users/test/proj"}) + "\n"
    )
    assert resolve_project_slug(slug) == Path("/Users/test/proj")


def test_resolve_project_slug_skips_malformed_json_lines(
    monkeypatch, tmp_path
) -> None:
    """Defensive: corrupt JSONL lines (truncated writes, etc.)
    must not raise — the function moves on and tries the next
    line / file."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-proj"
    project_dir = tmp_path / ".claude" / "projects" / slug
    project_dir.mkdir(parents=True)
    (project_dir / "session.jsonl").write_text(
        "this is not json\n"
        + "{broken: json\n"
        + json.dumps({"type": "user", "cwd": "/Users/test/proj"}) + "\n"
    )
    assert resolve_project_slug(slug) == Path("/Users/test/proj")


def test_resolve_project_slug_cached(monkeypatch, tmp_path) -> None:
    """Cache hit on the second call — the JSONL files should be
    opened only on the first resolution of a given slug."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-proj"
    cwd = "/Users/test/proj"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug, cwd=cwd
    )

    open_count = {"n": 0}
    original_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            open_count["n"] += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    resolve_project_slug.cache_clear()
    first = resolve_project_slug(slug)
    calls_after_first = open_count["n"]
    second = resolve_project_slug(slug)
    calls_after_second = open_count["n"]
    assert first == second == Path(cwd)
    assert calls_after_first > 0, "first call must read the JSONL"
    assert calls_after_second == calls_after_first, (
        "second call must hit the cache, not re-open the JSONL"
    )


# ---------- project_display_name ----------


def test_project_display_name_basename_for_resolved_path(
    monkeypatch, tmp_path
) -> None:
    """When the slug's JSONL yields a cwd, render the cwd's basename."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-Documents-tokenscope"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug,
        cwd="/Users/test/Documents/tokenscope",
    )
    assert project_display_name(slug) == "tokenscope"


def test_project_display_name_basename_preserves_hyphenated_dir(
    monkeypatch, tmp_path
) -> None:
    """The cwd preserves the original directory name verbatim, so
    a repo literally named `baremetal-audit` renders as
    `baremetal-audit` — not the wrong `audit` leaf the old
    basename heuristic would have produced."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-test-baremetal-audit"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug,
        cwd="/Users/test/baremetal-audit",
    )
    assert project_display_name(slug) == "baremetal-audit"


def test_project_display_name_tilde_for_macos_home(
    monkeypatch, tmp_path
) -> None:
    """Cwd of the form `/Users/<single-segment>` renders as `~`,
    not as the username. The heuristic is environment-independent
    (works in Docker where container HOME is `/root` but the
    host's HOME under `Path.home()` would still be wrong)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-alice"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug, cwd="/Users/alice"
    )
    assert project_display_name(slug) == "~"


def test_project_display_name_tilde_for_linux_home(
    monkeypatch, tmp_path
) -> None:
    """`/home/<single-segment>` renders as `~` on Linux paths."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-home-bob"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug, cwd="/home/bob"
    )
    assert project_display_name(slug) == "~"


def test_project_display_name_deeper_home_path_is_not_tilde(
    monkeypatch, tmp_path
) -> None:
    """`/Users/alice/Documents` (more than 3 parts) is NOT the home
    dir — it's a project under home. Should render as basename
    (`Documents`), not `~`."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    slug = "-Users-alice-Documents"
    _write_session_jsonl(
        tmp_path / ".claude" / "projects" / slug, cwd="/Users/alice/Documents"
    )
    assert project_display_name(slug) == "Documents"


def test_project_display_name_falls_back_to_friendly_label(
    monkeypatch, tmp_path
) -> None:
    """When `resolve_project_slug` returns None (no JSONL with
    cwd), fall back to `friendly_project_label` so the column
    still renders something readable. With the container's home
    not matching the slug's encoded path, the fallback strips the
    leading dash but doesn't substitute `~/...`."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home_slug.cache_clear()
    # No project directory at all → resolve returns None.
    result = project_display_name(
        "-Users-quintin-johnsmith-baremetal-audit"
    )
    # Container fallback: leading dash stripped, no `~/` because
    # tmp_path doesn't match the host's home prefix.
    assert result == "Users-quintin-johnsmith-baremetal-audit"


def test_project_display_name_empty_passthrough() -> None:
    """Empty slug passes through unchanged — no traceback, no
    synthetic placeholder."""
    assert project_display_name("") == ""
