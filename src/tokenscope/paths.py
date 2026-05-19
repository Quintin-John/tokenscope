"""Path utilities — package-level so any module can consume them.

Sits at the same architectural level as `tz.py` / `log.py` (small,
low-dependency utility modules). No Streamlit, no analytics, no
imports from `ui/`. That isolation is deliberate: `ui.sidebar` and
`ui.daily` both need these helpers, and if they lived in `ui/_data.py`
the resulting import chain (`sidebar → _data → sidebar`) would
deadlock at module-load time.

Public API:

  * `home_slug()`              — current user's home dir, slugified
                                 the way ccusage encodes project keys
  * `friendly_project_label()` — slug → home-relative display string
                                 (`~/...`). The fallback path inside
                                 `project_display_name` when the disk
                                 can't resolve the slug to a real dir.
                                 Originally lived in `analytics.py`;
                                 moved here because it's a path/slug
                                 helper, not an analytics rollup.
  * `resolve_project_slug()`   — slug → `Path` via greedy filesystem
                                 walk. The disk is the authoritative
                                 tiebreaker for ccusage's lossy
                                 `-`-encoding.
  * `project_display_name()`   — authoritative project-name rule for
                                 the dashboard. Resolves to basename
                                 when the path exists; falls back to
                                 `friendly_project_label` otherwise.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path


# Parent-dir names that mark a `/<parent>/<single-segment>` path as
# "the user's home directory" on a POSIX host. `_is_home_dir` matches
# against this tuple; tests for `project_display_name` exercise both
# variants. Single source for the macOS-vs-Linux home-parent rule.
_HOME_DIR_PARENT_NAMES = ("Users", "home")

# Env var ccusage and Claude Code itself honour for redirecting the
# `~/.claude` config directory. tokenscope reads it for the same
# reason: a user who's moved their Claude config (multi-host setup,
# alternate profile, custom mount) expects every tool that touches
# that data to follow the redirection. Honoured in `_claude_config_dir`.
_CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"


def _claude_config_dir() -> Path:
    """Resolve Claude Code's config directory.

    Returns `$CLAUDE_CONFIG_DIR` when set (matching ccusage / Claude
    Code's own override convention), else `~/.claude`. Single source
    for the config-dir lookup so any future ingestion path that
    needs to read JSONL transcripts, settings, or other Claude Code
    state honours the same redirection.
    """
    override = os.environ.get(_CLAUDE_CONFIG_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".claude"


@lru_cache(maxsize=1)
def home_slug() -> str:
    """Slug the current user's home dir the way ccusage encodes paths.

    `/Users/quintin-johnsmith` → `-Users-quintin-johnsmith`. Plumbed
    into `friendly_project_label(slug, home_slug_value=...)` so the
    rendered Project value substitutes the prefix with `~`.

    Cached because `Path.home()` is a system property that doesn't
    change over a process lifetime — recomputing it per render adds
    nothing. The lru_cache is process-scoped, NOT Streamlit-cached,
    so tests that monkeypatch `Path.home()` / `$HOME` must clear it
    via `home_slug.cache_clear()` to observe the new value.
    """
    return "-" + str(Path.home()).lstrip("/").replace("/", "-")


def friendly_project_label(slug: str, home_slug_value: str | None = None) -> str:
    """Make a ccusage project slug scannable.

    ccusage encodes a project's absolute path as a slugified string with
    `-` separators (e.g. `-Users-q-johnsmith-Documents-RiderProjects-WorldForge`).
    The encoding is *lossy* — hyphens inside directory names collide
    with the separator. `mini-ollama-ui` looks identical to `mini/ollama/ui`
    in slug form, so any "split on `-` and label the last bit" heuristic
    invents wrong leaves the moment a project name contains a hyphen.

    We deliberately do **not** try to recover directory structure. Instead:

    1. If the slug matches the user's home-directory slug (passed in as
       `home_slug_value` — `project_display_name` computes it from
       `home_slug()`), substitute the home prefix with `~`. That's
       where 90% of the sidebar noise lives.
    2. Otherwise, strip the leading `-` and leave the rest verbatim.
       The user reads "Volumes-SSK-Drive--Foo" and instantly recognises
       it as their external drive without us mangling it further.

    The parameter is the already-evaluated slug string, not the
    module-level `home_slug` function — `_value` suffix marks the
    distinction so the function body never collides with the
    module-level callable.

    Examples (with `home_slug_value="-Users-q-johnsmith"`):
        "-Users-q-johnsmith"                              → "~"
        "-Users-q-johnsmith-Documents-RiderProjects-tok"  → "~/Documents-RiderProjects-tok"
        "-Users-q-johnsmith-baremetal-audit"              → "~/baremetal-audit"
        "-Volumes-SSK-Drive--ManageLiterature"            → "Volumes-SSK-Drive--ManageLiterature"
        "Unknown Project"                                  → "Unknown Project"   (pass-through)
        ""                                                 → ""                  (pass-through)
    """
    if not slug:
        return slug
    if home_slug_value:
        if slug == home_slug_value:
            return "~"
        prefix = home_slug_value + "-"
        if slug.startswith(prefix):
            return "~/" + slug[len(prefix):]
    if slug.startswith("-"):
        return slug[1:]
    return slug


@lru_cache(maxsize=256)
def resolve_project_slug(slug: str) -> Path | None:
    """Read the authoritative cwd of a ccusage project slug from
    Claude Code's JSONL transcript files.

    Each session under `~/.claude/projects/<slug>/*.jsonl` carries
    a `cwd` field on its substantive records — the absolute path
    Claude Code ran in. The first JSONL entry with a `cwd` wins;
    they're the same value across a project's sessions.

    Returns a `Path` to that cwd, or None when:
      - `slug` is empty
      - the slug's project directory doesn't exist (no transcripts
        on this host)
      - no JSONL file in the directory parses to a record with a
        `cwd` field (defensively — this would mean ccusage emitted
        a slug we can't corroborate from its source data)

    This replaces an earlier filesystem-walk implementation that
    decoded the slug back into a path and `is_dir()`-checked each
    candidate. That worked on the host but broke in Docker: the
    container runs as root with `$HOME/.claude` mounted read-only
    at `/root/.claude`, and the host's `/Users/...` cwd encoded
    inside the slug doesn't exist on the container's filesystem.
    The JSONL `cwd` field is the same data the slug is encoded
    from, served by a file that IS available in the container.
    One source of truth, works in both environments.

    Config dir resolution goes through `_claude_config_dir()` so
    a `$CLAUDE_CONFIG_DIR` override (Claude Code's own convention
    for an alternate config root) routes to the right place.

    Cached per slug. Tests that monkeypatch the filesystem or set
    `$CLAUDE_CONFIG_DIR` must call `resolve_project_slug.cache_clear()`
    between scenarios.
    """
    if not slug:
        return None
    project_dir = _claude_config_dir() / "projects" / slug
    if not project_dir.is_dir():
        return None
    for jsonl in sorted(project_dir.glob("*.jsonl")):
        try:
            with jsonl.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = record.get("cwd")
                    if cwd:
                        return Path(cwd)
        except OSError:
            continue
    return None


def _is_home_dir(path: Path) -> bool:
    """Heuristic: a path is "the user's home directory" iff it has
    exactly three parts (`/`, `<parent>`, `<single-segment>`) and
    `<parent>` appears in `_HOME_DIR_PARENT_NAMES` (`Users` on
    macOS, `home` on Linux).

    Used to render `~` instead of the username basename when a
    project ran directly from the user's home dir. Container HOME
    (`/root`) doesn't equal the host's HOME (`/Users/<user>`), so
    a direct `Path.home()` comparison wouldn't fire in Docker —
    the heuristic is environment-independent.

    Anything deeper than three parts (`/Users/<user>/Documents/...`)
    is a project under home, not the home dir itself.
    """
    parts = path.parts
    return (
        len(parts) == 3
        and parts[0] == "/"
        and parts[1] in _HOME_DIR_PARENT_NAMES
    )


def project_display_name(slug: str) -> str:
    """Authoritative project-name display rule for the dashboard.

    Resolution order:
      1. `resolve_project_slug(slug)` returns a cwd → render `~`
         for the user's home dir (per `_is_home_dir`), else the
         path's basename.
      2. JSONL doesn't yield a cwd → fall back to
         `friendly_project_label(slug, home_slug_value=home_slug())`.
         The slug appeared in ccusage's data but the transcripts
         are unavailable (mount missing, files trimmed, etc.); we
         still render *something* readable.

    Empty / falsy slug passes through unchanged.

    Used by:
      - Daily view's Project column
      - Sidebar's Project dropdown
    One rule, two consumers — DRY/SOLID anchor for project
    display across the dashboard.
    """
    if not slug:
        return slug
    resolved = resolve_project_slug(slug)
    if resolved is not None:
        if _is_home_dir(resolved):
            return "~"
        return resolved.name
    return friendly_project_label(slug, home_slug_value=home_slug())
