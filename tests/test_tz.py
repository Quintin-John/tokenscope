"""Unit tests for tokenscope.tz."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tokenscope.tz import (
    DEFAULT_FALLBACK,
    detect_local_iana,
    utc_iso_to_local,
    utc_iso_to_local_clock,
    utc_iso_to_local_date,
)


def test_detect_local_iana_returns_some_string(monkeypatch) -> None:
    """Whatever the host environment, we must return a non-empty string —
    every consumer can pass the result straight to ccusage without
    nil-checks. TZ unset so the test isn't accidentally exercising the
    env-var probe (which is covered separately)."""
    monkeypatch.delenv("TZ", raising=False)
    name = detect_local_iana()
    assert isinstance(name, str)
    assert name  # never empty


def test_detect_local_iana_honours_tz_env(monkeypatch) -> None:
    """A valid IANA name in TZ must be returned verbatim — this is the
    Docker-container path. The slim Python images symlink
    /etc/localtime → Etc/UTC, so without an explicit TZ check we'd
    silently bucket data in UTC even when the user passes
    -e TZ=America/New_York. The TZ probe is what makes the user's
    flag take effect."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert detect_local_iana() == "Asia/Tokyo"


def test_detect_local_iana_ignores_posix_tz(monkeypatch) -> None:
    """A POSIX-style TZ rule (e.g. ``EST5EDT,M3.2.0,M11.1.0``) is *not*
    an IANA zone — passing it to ccusage would error. Fall through to
    the next probe instead of returning the rule string."""
    import tokenscope.tz as tz_mod

    monkeypatch.setenv("TZ", "EST5EDT,M3.2.0,M11.1.0")
    # Force the symlink probe to a known IANA target so we can verify
    # we didn't return the POSIX junk.
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: True, raising=False
    )
    monkeypatch.setattr(
        tz_mod.os, "readlink",
        lambda _: "/usr/share/zoneinfo/America/Chicago",
    )
    name = detect_local_iana()
    assert name == "America/Chicago"
    assert "," not in name  # Definitely not the POSIX rule string.


def test_detect_local_iana_empty_tz_falls_through(monkeypatch) -> None:
    """TZ set to "" (e.g. a Docker compose-file with ``TZ:`` left blank)
    must not satisfy the env probe — `.strip()` on an empty string is
    falsy so the if-guard skips it and the symlink probe runs instead."""
    import tokenscope.tz as tz_mod

    monkeypatch.setenv("TZ", "")
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: True, raising=False
    )
    monkeypatch.setattr(
        tz_mod.os, "readlink",
        lambda _: "/usr/share/zoneinfo/Europe/Berlin",
    )
    assert detect_local_iana() == "Europe/Berlin"


def test_detect_local_iana_whitespace_only_tz_falls_through(monkeypatch) -> None:
    """TZ set to whitespace ("   ") is functionally unset — `.strip()`
    normalises it. Skipping the env probe avoids handing whitespace to
    ZoneInfo, which would raise and we'd swallow."""
    import tokenscope.tz as tz_mod

    monkeypatch.setenv("TZ", "   ")
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: True, raising=False
    )
    monkeypatch.setattr(
        tz_mod.os, "readlink",
        lambda _: "/usr/share/zoneinfo/Pacific/Auckland",
    )
    assert detect_local_iana() == "Pacific/Auckland"


def test_detect_local_iana_malformed_iana_falls_through(monkeypatch) -> None:
    """A typo'd IANA-looking value (`Atlantis/Lost_City`) is not a real
    zone — ZoneInfo raises, we fall through rather than return junk
    that would crash ccusage."""
    import tokenscope.tz as tz_mod

    monkeypatch.setenv("TZ", "Atlantis/Lost_City")
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: True, raising=False
    )
    monkeypatch.setattr(
        tz_mod.os, "readlink",
        lambda _: "/usr/share/zoneinfo/Asia/Tokyo",
    )
    assert detect_local_iana() == "Asia/Tokyo"


def test_detect_local_iana_path_like_tz_falls_through(monkeypatch) -> None:
    """TZ set to a path (`/etc/foo`) — ZoneInfo treats it as a key,
    can't find it, raises. Must fall through."""
    import tokenscope.tz as tz_mod

    monkeypatch.setenv("TZ", "/etc/foo")
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: True, raising=False
    )
    monkeypatch.setattr(
        tz_mod.os, "readlink",
        lambda _: "/usr/share/zoneinfo/America/Denver",
    )
    assert detect_local_iana() == "America/Denver"


def test_detect_local_iana_bare_utc_in_tz_returns_utc(monkeypatch) -> None:
    """TZ="UTC" is a valid IANA zone (ZoneInfo accepts it). Returned as
    the literal "UTC" — no '/' in the name, but still authoritative."""
    monkeypatch.setenv("TZ", "UTC")
    assert detect_local_iana() == "UTC"


def test_detect_local_iana_uses_astimezone_key_when_tz_unset(monkeypatch) -> None:
    """Probe 2: when TZ is unset and the host's stdlib resolves a
    ZoneInfo-typed tzinfo with a ``.key`` containing a '/', that key
    is returned. Covers the production path on macOS native runs."""
    monkeypatch.delenv("TZ", raising=False)
    import tokenscope.tz as tz_mod

    class _ZoneInfoLikeTz:
        key = "Europe/Paris"

        def utcoffset(self, dt):
            return None

        def tzname(self, dt):
            return "CET"

        def dst(self, dt):
            return None

    class _FakeDatetime:
        @staticmethod
        def now():
            class _D:
                def astimezone(self):
                    class _R:
                        tzinfo = _ZoneInfoLikeTz()

                    return _R()

            return _D()

    monkeypatch.setattr(tz_mod, "datetime", _FakeDatetime)
    assert detect_local_iana() == "Europe/Paris"


def test_detect_local_iana_readlink_oserror_falls_through_to_utc(
    monkeypatch,
) -> None:
    """Probe 3 defensive path: if ``/etc/localtime`` is a symlink but
    ``os.readlink`` raises OSError (corrupt symlink, EACCES, ELOOP),
    swallow it and continue to the UTC fallback rather than crashing
    the whole sidebar render."""
    monkeypatch.delenv("TZ", raising=False)
    import tokenscope.tz as tz_mod

    class _NoKeyTzInfo:
        def utcoffset(self, dt):
            return None

        def tzname(self, dt):
            return "X"

        def dst(self, dt):
            return None

    class _FakeDatetime:
        @staticmethod
        def now():
            class _D:
                def astimezone(self):
                    class _R:
                        tzinfo = _NoKeyTzInfo()

                    return _R()

            return _D()

    monkeypatch.setattr(tz_mod, "datetime", _FakeDatetime)
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: True, raising=False
    )

    def _raise_oserror(_):
        raise OSError("EACCES on /etc/localtime")

    monkeypatch.setattr(tz_mod.os, "readlink", _raise_oserror)
    assert detect_local_iana() == DEFAULT_FALLBACK


def test_detect_local_iana_symlink_fallback(monkeypatch, tmp_path) -> None:
    """If `datetime.astimezone().tzinfo` doesn't have a `key` attr, we
    fall back to reading the `/etc/localtime` symlink target."""
    monkeypatch.delenv("TZ", raising=False)
    # Patch the datetime path to force the fallback.
    import tokenscope.tz as tz_mod

    class _NoKeyTzInfo:
        utcoffset = lambda self, dt: None
        tzname = lambda self, dt: "X"
        dst = lambda self, dt: None

    class _NoKeyDatetime:
        @staticmethod
        def now():
            class _D:
                def astimezone(self):
                    class _R:
                        tzinfo = _NoKeyTzInfo()

                    return _R()

            return _D()

    monkeypatch.setattr(tz_mod, "datetime", _NoKeyDatetime)
    # Make /etc/localtime appear to be a symlink pointing at an IANA zone.
    fake_target = "/var/db/timezone/zoneinfo/America/Los_Angeles"
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: True, raising=False
    )
    monkeypatch.setattr(tz_mod.os, "readlink", lambda _: fake_target)
    assert detect_local_iana() == "America/Los_Angeles"


def test_detect_local_iana_returns_utc_when_all_else_fails(monkeypatch) -> None:
    """Force the most pessimistic path: tzinfo has no key, no symlink."""
    monkeypatch.delenv("TZ", raising=False)
    import tokenscope.tz as tz_mod

    class _NoKeyTzInfo:
        pass

    class _NoKeyDatetime:
        @staticmethod
        def now():
            class _D:
                def astimezone(self):
                    class _R:
                        tzinfo = _NoKeyTzInfo()

                    return _R()

            return _D()

    monkeypatch.setattr(tz_mod, "datetime", _NoKeyDatetime)
    monkeypatch.setattr(
        tz_mod.Path, "is_symlink", lambda self: False, raising=False
    )
    assert detect_local_iana() == DEFAULT_FALLBACK


# ---------- utc_iso_to_local ----------


def test_utc_iso_to_local_basic() -> None:
    """ccusage emits Z-suffixed UTC; we convert to the user's zone with
    the abbreviation appended."""
    s = utc_iso_to_local("2026-05-16T13:00:00.000Z", "America/Los_Angeles")
    # 13:00 UTC on 2026-05-16 → 06:00 PDT (UTC-7 during DST).
    assert s is not None
    assert "2026-05-16 06:00:00" in s
    assert "PDT" in s or "PST" in s


def test_utc_iso_to_local_empty_returns_none() -> None:
    assert utc_iso_to_local("", "UTC") is None


def test_utc_iso_to_local_malformed_returns_none() -> None:
    assert utc_iso_to_local("not-a-timestamp", "America/Los_Angeles") is None


def test_utc_iso_to_local_unknown_zone_returns_raw_iso() -> None:
    """Unknown zone names raise ZoneInfoNotFoundError; we return the
    raw input so the UI still has something to render. Narrow catch:
    a corrupt-tzdata OSError would surface as a real bug, not get
    swallowed silently."""
    s = utc_iso_to_local("2026-05-16T13:00:00.000Z", "Atlantis/Lost_City")
    assert s == "2026-05-16T13:00:00.000Z"


def test_utc_iso_to_local_malformed_zone_returns_raw_iso() -> None:
    """An absolute-path zone key raises ValueError, not
    ZoneInfoNotFoundError. Must also fall back rather than crash."""
    s = utc_iso_to_local("2026-05-16T13:00:00.000Z", "/etc/foo")
    assert s == "2026-05-16T13:00:00.000Z"


# ---------- utc_iso_to_local_date ----------


def test_utc_iso_to_local_date_crosses_day_boundary() -> None:
    """A block at 06:00 UTC on May 17 is May 16 in Los Angeles
    (UTC-7 during DST). The local date should reflect that."""
    d = utc_iso_to_local_date("2026-05-17T06:00:00.000Z", "America/Los_Angeles")
    assert d == "2026-05-16"


def test_utc_iso_to_local_date_no_crossover() -> None:
    """A mid-afternoon UTC time stays on the same UTC date in Los Angeles."""
    d = utc_iso_to_local_date("2026-05-16T20:00:00.000Z", "America/Los_Angeles")
    # 20:00 UTC → 13:00 PDT, still May 16.
    assert d == "2026-05-16"


def test_utc_iso_to_local_date_utc_zone_passthrough() -> None:
    """Asking for the date in UTC just slices the prefix."""
    d = utc_iso_to_local_date("2026-05-16T13:00:00.000Z", "UTC")
    assert d == "2026-05-16"


def test_utc_iso_to_local_date_empty() -> None:
    assert utc_iso_to_local_date("", "UTC") is None


def test_utc_iso_to_local_date_malformed() -> None:
    assert utc_iso_to_local_date("garbage", "America/Los_Angeles") is None


def test_utc_iso_to_local_date_unknown_zone_returns_iso_prefix() -> None:
    """Unknown zone → return the UTC date-prefix as a last-resort label."""
    d = utc_iso_to_local_date("2026-05-16T13:00:00.000Z", "Atlantis/Lost_City")
    assert d == "2026-05-16"


def test_utc_iso_to_local_date_malformed_zone_returns_iso_prefix() -> None:
    """Malformed zone (ValueError) → same fall-back as unknown zone."""
    d = utc_iso_to_local_date("2026-05-16T13:00:00.000Z", "/etc/foo")
    assert d == "2026-05-16"


# ---------- utc_iso_to_local_clock ----------


def test_utc_iso_to_local_clock_returns_hh_mm() -> None:
    """`HH:MM` format — used by the Live window banner where only the
    clock time matters (the date is implicit on a 5-hour window)."""
    # 13:00 UTC == 09:00 EDT in May (UTC-4 with DST).
    clock = utc_iso_to_local_clock(
        "2026-05-16T13:00:00.000Z", "America/New_York"
    )
    assert clock == "09:00"


def test_utc_iso_to_local_clock_utc_zone() -> None:
    """UTC zone → passes the wall-clock time through unchanged."""
    assert (
        utc_iso_to_local_clock("2026-05-16T13:00:00.000Z", "UTC") == "13:00"
    )


def test_utc_iso_to_local_clock_empty_input_returns_none() -> None:
    assert utc_iso_to_local_clock("", "America/New_York") is None


def test_utc_iso_to_local_clock_malformed_iso_returns_none() -> None:
    assert utc_iso_to_local_clock("garbage", "America/New_York") is None


def test_utc_iso_to_local_clock_unknown_zone_returns_raw_iso() -> None:
    """Defensive: unknown zone falls back to the raw input rather
    than crashing the live view's banner render."""
    out = utc_iso_to_local_clock("2026-05-16T13:00:00.000Z", "Atlantis/Lost")
    assert out == "2026-05-16T13:00:00.000Z"
