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
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tokenscope.log import get_logger

_log = get_logger(__name__)

DEFAULT_FALLBACK = "UTC"
_LOCALTIME_MARKER = "/zoneinfo/"
# ZoneInfo(key) raises ZoneInfoNotFoundError for unknown IANA names and
# ValueError for malformed keys (absolute paths, empty strings, etc.).
# We catch both as "this isn't a usable zone, fall through" — anything
# else is a genuine bug and must surface.
_ZONE_INVALID = (ZoneInfoNotFoundError, ValueError)


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
            ZoneInfo(tz_env)
            _log.info("tz.detected source=env_var zone=%s", tz_env)
            return tz_env
        except _ZONE_INVALID as exc:
            # TZ set to a POSIX-style string ("EST5EDT,M3.2.0,M11.1.0"),
            # an absolute path, an empty string, or junk — fall through
            # to the other probes rather than confidently returning
            # garbage. Anything that isn't a zone-resolution failure
            # (e.g. an OSError from a corrupt tzdata file) surfaces.
            _log.warning(
                "tz.probe.env_invalid value=%r reason=%s — falling back to OS",
                tz_env,
                exc,
            )

    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    if isinstance(key, str) and "/" in key:
        _log.info("tz.detected source=os_astimezone zone=%s", key)
        return key

    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        try:
            target = os.readlink(localtime)
        except OSError:
            target = ""
        if _LOCALTIME_MARKER in target:
            resolved = target.rsplit(_LOCALTIME_MARKER, 1)[1]
            _log.info("tz.detected source=etc_localtime zone=%s", resolved)
            return resolved
    _log.warning(
        "tz.fallback_to_utc all_probes_failed — ccusage will bucket by UTC"
    )
    return DEFAULT_FALLBACK


# --- shared parse + zone helpers ---------------------------------------
#
# Every public `utc_iso_to_local_*` wrapper below shares the same two
# operations: parse a UTC ISO timestamp (with ccusage's trailing `Z`)
# into a tz-aware datetime, then convert it into a target IANA zone.
# Pulling them into named helpers means there is exactly one place that
# knows the ccusage ISO quirk (the `Z` → `+00:00` substitution) and one
# place that knows the zone-failure contract — adding a new local-format
# wrapper is one strftime line, not a 7-line ceremony.


def _parse_utc(iso: str) -> datetime | None:
    """Parse a UTC ISO timestamp (with trailing `Z`) into a tz-aware
    datetime.

    Returns ``None`` for empty / malformed input — every wrapper
    treats this as "data missing, hide the field" rather than
    raising. Anything else (corrupt tzdata file, OSError) propagates,
    since those represent genuine bugs the user should see.

    The `Z` → `+00:00` substitution is the one-line workaround for
    Python ≤ 3.10's `fromisoformat` rejecting the bare `Z` marker
    ccusage emits.
    """
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_local(utc_dt: datetime, zone: str) -> datetime | None:
    """Convert a tz-aware UTC datetime into the given IANA zone.

    Returns ``None`` when the zone is unknown, malformed, or an
    empty string. Each wrapper decides its own fallback (raw ISO,
    date prefix, etc.) — the helper just signals "couldn't convert".
    """
    try:
        return utc_dt.astimezone(ZoneInfo(zone))
    except _ZONE_INVALID:
        return None


# --- public conversion wrappers ----------------------------------------


def utc_iso_to_local(iso: str, zone: str) -> str | None:
    """Convert a UTC ISO timestamp (with trailing `Z`) to a local-time
    string in the user's IANA zone.

    Returns ``None`` when parsing fails (defensive — ccusage's output
    has been stable, but a corrupted block id shouldn't crash a view).
    Falls back to the raw ISO input when the zone itself can't be
    resolved.

    Example:
        utc_iso_to_local("2026-05-16T13:00:00.000Z", "America/Los_Angeles")
            → "2026-05-16 06:00:00 PDT"
    """
    utc_dt = _parse_utc(iso)
    if utc_dt is None:
        return None
    local_dt = _to_local(utc_dt, zone)
    if local_dt is None:
        # Unknown / malformed zone — caller passed something we can't
        # resolve. Return the raw input rather than crashing the view.
        return iso
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def utc_iso_to_local_clock(iso: str, zone: str) -> str | None:
    """Return just the local-zone `HH:MM` clock time of a UTC ISO
    timestamp. Used by the Live view's window banner where the date
    is implicit (the active block always covers the current day's
    5-hour slice) and only the start/end clock times need to render.
    """
    utc_dt = _parse_utc(iso)
    if utc_dt is None:
        return None
    local_dt = _to_local(utc_dt, zone)
    if local_dt is None:
        return iso
    return local_dt.strftime("%H:%M")


def utc_iso_to_local_naive_iso(iso: str, zone: str) -> str | None:
    """Convert a UTC ISO timestamp to a *naive* local-clock ISO string
    suitable for handing directly to Plotly's date-axis machinery
    without re-interpretation.

    The trailing ``Z`` (UTC marker) and any timezone offset are
    stripped from the output. Plotly treats the resulting
    ``YYYY-MM-DDTHH:MM:SS`` value as a naive datetime and renders
    the axis tick at exactly that clock value — i.e. the user's
    wall-clock time in their zone, no automatic re-conversion.

    This is the one-line answer to "why does my chart axis say UTC
    when the rest of the page says EDT": Plotly silently coerces
    any ``...Z`` value into the browser's locale. Naive ISO sidesteps
    the coercion entirely.

    Returns ``None`` for empty / malformed input; the raw ISO is
    returned if the zone itself can't be resolved.

    Example:
        utc_iso_to_local_naive_iso(
            "2026-05-17T19:00:00.000Z", "America/New_York"
        )
            → "2026-05-17T15:00:00"   (3pm EDT, the wall-clock time)
    """
    utc_dt = _parse_utc(iso)
    if utc_dt is None:
        return None
    local_dt = _to_local(utc_dt, zone)
    if local_dt is None:
        return iso
    return local_dt.strftime("%Y-%m-%dT%H:%M:%S")


def minutes_since_utc_iso(iso: str, now_utc: datetime | None = None) -> float | None:
    """Minutes elapsed between a UTC ISO timestamp and ``now_utc``.

    Used by the Live view's throughput-chart empty state to decide
    whether enough wall-clock time has passed for the bucketing
    chart to be informative — when the active block has just started,
    a percent-stacked area of one bucket is a degenerate single
    column and the chart layer renders a "block too new" caption
    instead.

    ``now_utc`` defaults to ``datetime.now(timezone.utc)`` so the
    test surface can inject a frozen instant. Returns ``None`` for
    empty / unparseable input.
    """
    utc_dt = _parse_utc(iso)
    if utc_dt is None:
        return None
    reference = now_utc if now_utc is not None else datetime.now(timezone.utc)
    return (reference - utc_dt).total_seconds() / 60.0


def utc_iso_to_local_date(iso: str, zone: str) -> str | None:
    """Return just the YYYY-MM-DD local-zone date of a UTC ISO timestamp.

    Used by `analytics.blocks_on_day` to bucket a block by its local
    start-of-day rather than its UTC date. Falls back to the UTC
    date prefix when the zone can't be resolved — a last-resort
    label rather than crashing the view.
    """
    utc_dt = _parse_utc(iso)
    if utc_dt is None:
        return None
    local_dt = _to_local(utc_dt, zone)
    if local_dt is None:
        return iso[:10]
    return local_dt.strftime("%Y-%m-%d")
