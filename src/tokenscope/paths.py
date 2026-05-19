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

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def home_slug() -> str:
    """Slug the current user's home dir the way ccusage encodes paths.

    `/Users/quintin-johnsmith` → `-Users-quintin-johnsmith`. Plumbed
    into `friendly_project_label(slug, home_slug=...)` so the
    rendered Project value substitutes the prefix with `~`.

    Cached because `Path.home()` is a system property that doesn't
    change over a process lifetime — recomputing it per render adds
    nothing. The lru_cache is process-scoped, NOT Streamlit-cached,
    so tests that monkeypatch `Path.home()` / `$HOME` must clear it
    via `home_slug.cache_clear()` to observe the new value.
    """
    return "-" + str(Path.home()).lstrip("/").replace("/", "-")


def friendly_project_label(slug: str, home_slug: str | None = None) -> str:
    """Make a ccusage project slug scannable.

    ccusage encodes a project's absolute path as a slugified string with
    `-` separators (e.g. `-Users-q-johnsmith-Documents-RiderProjects-WorldForge`).
    The encoding is *lossy* — hyphens inside directory names collide
    with the separator. `mini-ollama-ui` looks identical to `mini/ollama/ui`
    in slug form, so any "split on `-` and label the last bit" heuristic
    invents wrong leaves the moment a project name contains a hyphen.

    We deliberately do **not** try to recover directory structure. Instead:

    1. If the slug matches the user's home-directory slug (passed in as
       `home_slug` — sidebar.py computes it from `pathlib.Path.home()`),
       substitute the home prefix with `~`. That's where 90% of the
       sidebar noise lives.
    2. Otherwise, strip the leading `-` and leave the rest verbatim.
       The user reads "Volumes-SSK-Drive--Foo" and instantly recognises
       it as their external drive without us mangling it further.

    The parameter name `home_slug` deliberately shadows the module-level
    `home_slug()` function inside this scope — harmless because the
    function body never calls `home_slug()`; every caller passes the
    already-evaluated string in.

    Examples (with `home_slug="-Users-q-johnsmith"`):
        "-Users-q-johnsmith"                              → "~"
        "-Users-q-johnsmith-Documents-RiderProjects-tok"  → "~/Documents-RiderProjects-tok"
        "-Users-q-johnsmith-baremetal-audit"              → "~/baremetal-audit"
        "-Volumes-SSK-Drive--ManageLiterature"            → "Volumes-SSK-Drive--ManageLiterature"
        "Unknown Project"                                  → "Unknown Project"   (pass-through)
        ""                                                 → ""                  (pass-through)
    """
    if not slug:
        return slug
    if home_slug:
        if slug == home_slug:
            return "~"
        prefix = home_slug + "-"
        if slug.startswith(prefix):
            return "~/" + slug[len(prefix):]
    if slug.startswith("-"):
        return slug[1:]
    return slug


@lru_cache(maxsize=256)
def resolve_project_slug(slug: str) -> Path | None:
    """Decode a ccusage project slug back to its filesystem path via
    a greedy longest-prefix walk against the real filesystem.

    At each path-segment boundary, take the longest dash-joined token
    sequence that exists as a real directory. The filesystem is the
    authoritative tiebreaker for ccusage's lossy `-`-encoding (a
    directory whose name contains `-` is otherwise indistinguishable
    from a nested path).

    Returns None when:
      - `slug` is empty or doesn't start with `-`
      - no decoding of `slug` reaches a real directory (drive
        unmounted, project deleted, etc.)

    Cached per slug. Tests that monkeypatch the filesystem must call
    `resolve_project_slug.cache_clear()` between scenarios.
    """
    if not slug or not slug.startswith("-"):
        return None
    tokens = slug[1:].split("-")
    current = Path("/")
    i = 0
    while i < len(tokens):
        matched_end: int | None = None
        # Try longest dash-joined name first; first match wins.
        for j in range(len(tokens), i, -1):
            name = "-".join(tokens[i:j])
            if not name:
                continue
            if (current / name).is_dir():
                matched_end = j
                break
        if matched_end is None:
            return None
        current = current / "-".join(tokens[i:matched_end])
        i = matched_end
    return current


def project_display_name(slug: str) -> str:
    """Authoritative project-name display rule for the dashboard.

    Resolution order:
      1. `resolve_project_slug(slug)` finds a real directory →
         return `Path.name` (basename). `~` when the resolved path
         is exactly `Path.home()`.
      2. No real directory matches → fall back to
         `friendly_project_label(slug, home_slug=home_slug())`. The
         project may have been deleted or its drive unmounted; the
         slug still appeared in ccusage's data, so we render
         *something* readable.

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
        if resolved == Path.home():
            return "~"
        return resolved.name
    return friendly_project_label(slug, home_slug=home_slug())
