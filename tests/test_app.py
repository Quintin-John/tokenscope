"""Unit tests for tokenscope.app — the entry-point module.

End-to-end rendering of every view is covered by `test_ui_smoke.py`.
This file holds focused tests on the pure helpers in `app.py` that
don't need a full Streamlit runtime — currently just `_build_about_text`.
"""

from __future__ import annotations

import pytest

from tokenscope import app, ccusage


def test_build_about_text_includes_version_on_success(monkeypatch) -> None:
    """Happy path: when `get_ccusage_version` returns a string, the
    About blurb surfaces it verbatim (no inline-code styling).

    Note: `app.py` imports `get_ccusage_version` by name at module
    load, so we patch the binding inside `app`, not the source module.
    """
    monkeypatch.setattr(app, "get_ccusage_version", lambda: "18.0.11")
    text = app._build_about_text()
    assert "ccusage version: 18.0.11" in text
    assert "`" not in text.split("ccusage version:")[1].split("\n")[0], (
        "version should be plain text, not wrapped in backticks"
    )


def test_build_about_text_renders_fallback_on_ccusage_error(monkeypatch) -> None:
    """When ccusage isn't installed or its `--version` shell-out
    fails, the About blurb still renders — the page-config call
    can't propagate the error."""
    def _raise() -> str:
        raise ccusage.CcusageError("simulated bridge failure")

    monkeypatch.setattr(app, "get_ccusage_version", _raise)
    text = app._build_about_text()
    assert "ccusage version: unavailable" in text


def test_build_about_text_mentions_tz_override() -> None:
    """The blurb is where the `TZ` env-var instruction lives — moved
    out of the sidebar caption so the panel doesn't read as CLI docs."""
    # No monkeypatch — uses whatever ccusage version is on disk, which
    # we don't care about for this assertion.
    text = app._build_about_text()
    assert "TZ" in text
    assert "timezone" in text.lower()
