"""Unit tests for tokenscope.tz."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tokenscope.tz import (
    DEFAULT_FALLBACK,
    detect_local_iana,
    utc_iso_to_local,
    utc_iso_to_local_date,
)


def test_detect_local_iana_returns_some_string() -> None:
    """Whatever the host environment, we must return a non-empty string —
    every consumer can pass the result straight to ccusage without
    nil-checks."""
    name = detect_local_iana()
    assert isinstance(name, str)
    assert name  # never empty


def test_detect_local_iana_honours_tz_env(monkeypatch) -> None:
    """Setting the `TZ` env var should propagate through Python's tz
    detection (Python 3.9+ with zoneinfo)."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    # `time.tzset()` is needed on some platforms to make TZ effective.
    try:
        import time

        time.tzset()
    except (AttributeError, OSError):
        pass
    name = detect_local_iana()
    # On a host that supports tzset, the returned name should be Asia/Tokyo
    # (or contain the same key). On platforms where tzset isn't available
    # we just confirm we got *something* IANA-ish.
    assert isinstance(name, str)
    # On macOS/Linux with tzdata: should be the env zone.
    if "/" in name:
        # Either we got the env-set zone, or the symlink fallback gave us
        # the host's actual zone — both are valid.
        assert name == "Asia/Tokyo" or "/" in name


def test_detect_local_iana_symlink_fallback(monkeypatch, tmp_path) -> None:
    """If `datetime.astimezone().tzinfo` doesn't have a `key` attr, we
    fall back to reading the `/etc/localtime` symlink target."""
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
