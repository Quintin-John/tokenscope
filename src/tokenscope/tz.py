"""Local-timezone detection + UTC↔local conversion helpers.

ccusage accepts a `--timezone=<IANA>` flag that controls how it buckets
daily / weekly / monthly entries. Without that flag, all timestamps and
day boundaries are UTC — which is fine for a UTC-resident user but
silently puts a PST user's "yesterday's blocks" under "tomorrow's date".

We auto-detect the user's IANA timezone once at sidebar render-time
and plumb it through `Query` into every ccusage call. The user can
override via the `TZ` env var (same mechanism ccusage's own users
already know), which `datetime.astimezone().tzinfo` honours.

This module is OS-touching (reads `/etc/localtime` on Unix), so it
lives outside `analytics.py` (which is pure).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

DEFAULT_FALLBACK = "UTC"
_LOCALTIME_MARKER = "/zoneinfo/"


def detect_local_iana() -> str:
    """Return the user's IANA timezone name, best-effort.

    Lookup order:
      1. ``TZ`` env var, if it names a valid IANA zone. Why first:
         in slim Docker images the host TZ is passed in via
         ``-e TZ=America/New_York``, but Python's
         ``datetime.now().astimezone()`` parses it into a fixed-offset
         ``datetime.timezone(..., 'EDT')`` object that has no ``.key``
         attribute — so probe (2) silently misses it. Reading TZ
         explicitly and validating via ``ZoneInfo`` skips that wart.
      2. `datetime.now().astimezone().tzinfo` — Python 3.9+ returns a
         `zoneinfo.ZoneInfo` with a `key` when the system tz database
         resolved a zone-named symlink. Works on macOS / non-Dockerised
         Linux; doesn't fire in slim containers (see above).
      3. `/etc/localtime` symlink target on Unix (macOS: typically
         `/var/db/timezone/zoneinfo/<Zone>`; Linux: `/usr/share/zoneinfo/<Zone>`).
         Extract everything after the last `/zoneinfo/` segment. In
         slim Docker images this points at `Etc/UTC` regardless of
         host zone — that's why probe (1) has to come first.
      4. Final fallback: "UTC" (never None — every consumer can pass
         the result straight to ccusage without nil-checks).
    """
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz_env)
            return tz_env
        except Exception:
            # TZ set to a POSIX-style string ("EST5EDT") or junk — fall
            # through to the other probes rather than confidently
            # returning garbage.
            pass

    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    if isinstance(key, str) and "/" in key:
        return key

    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        try:
            target = os.readlink(localtime)
        except OSError:
            target = ""
        if _LOCALTIME_MARKER in target:
            return target.rsplit(_LOCALTIME_MARKER, 1)[1]
    return DEFAULT_FALLBACK


def utc_iso_to_local(iso: str, zone: str) -> str | None:
    """Convert a UTC ISO timestamp (with trailing `Z`) to a local-time
    string in the user's IANA zone.

    Returns ``None`` when parsing fails (defensive — ccusage's output
    has been stable, but a corrupted block id shouldn't crash a view).

    Example:
        utc_iso_to_local("2026-05-16T13:00:00.000Z", "America/Los_Angeles")
            → "2026-05-16 06:00:00 PDT"
    """
    if not iso:
        return None
    try:
        # Python 3.11+ handles the trailing `Z`; older versions don't.
        utc_dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo

        local_dt = utc_dt.astimezone(ZoneInfo(zone))
    except (ImportError, Exception):
        return iso  # Best-effort: fall back to the original
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def utc_iso_to_local_date(iso: str, zone: str) -> str | None:
    """Return just the YYYY-MM-DD local-zone date of a UTC ISO timestamp.

    Used by `analytics.blocks_on_day` to bucket a block by its local
    start-of-day rather than its UTC date.
    """
    if not iso:
        return None
    try:
        utc_dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo

        return utc_dt.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d")
    except (ImportError, Exception):
        return iso[:10]
