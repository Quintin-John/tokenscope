"""Path utilities — package-level so any module can consume them.

Sits at the same architectural level as `tz.py` / `log.py` (small,
low-dependency utility modules). No Streamlit, no analytics, no
imports from `ui/`. That isolation is deliberate: `ui.sidebar` and
`ui.daily` both need `home_slug()`, and if it lived in `ui/_data.py`
the resulting import chain (`sidebar → _data → sidebar`) would
deadlock at module-load time.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def home_slug() -> str:
    """Slug the current user's home dir the way ccusage encodes paths.

    `/Users/quintin-johnsmith` → `-Users-quintin-johnsmith`. Plumbed
    into `analytics.friendly_project_label(slug, home_slug=...)` so
    the rendered Project value substitutes the prefix with `~`.

    Cached because `Path.home()` is a system property that doesn't
    change over a process lifetime — recomputing it per render adds
    nothing. The lru_cache is process-scoped, NOT Streamlit-cached,
    so tests that monkeypatch `Path.home()` / `$HOME` must clear it
    via `home_slug.cache_clear()` to observe the new value.
    """
    return "-" + str(Path.home()).lstrip("/").replace("/", "-")
