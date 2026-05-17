"""Shared pytest fixtures.

The `mock_ccusage` fixture replaces `tokenscope.ccusage._run_json` with
a deterministic in-memory mock backed by JSON fixtures in
`tests/fixtures/`. This is the unit-test path for every UI render —
no subprocess, no network, no `node_modules/.bin/ccusage` required.
Production code is **not** modified; the patch lives only in the fixture's
scope.

Usage:

    def test_overview(mock_ccusage):
        mock_ccusage("daily", response=FIXTURES / "daily.json")
        mock_ccusage("blocks", "--active", response={"blocks": []})
        # ... run AppTest / call analytics directly / etc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


_EMPTY_TOTALS: dict[str, Any] = {
    "inputTokens": 0,
    "outputTokens": 0,
    "cacheCreationTokens": 0,
    "cacheReadTokens": 0,
    "totalTokens": 0,
    "totalCost": 0,
}


def _default_empty_response(args: list[str]) -> dict:
    """Fallback response when no explicit mock matches a ccusage call.

    Returns the *empty* shape for the requested subcommand so the UI's
    "no data in window" code paths can be exercised without setup.
    """
    cmd = args[0] if args else ""
    if "--instances" in args:
        return {"projects": {}, "totals": dict(_EMPTY_TOTALS)}
    if cmd in ("daily", "weekly", "monthly"):
        return {cmd: [], "totals": dict(_EMPTY_TOTALS)}
    if cmd == "session":
        return {"sessions": [], "totals": dict(_EMPTY_TOTALS)}
    if cmd == "blocks":
        return {"blocks": []}
    raise AssertionError(
        f"Unmocked ccusage call {args!r}. Register a response via "
        "`mock_ccusage(<argv-prefix>, response=...)`."
    )


@pytest.fixture
def mock_ccusage(monkeypatch):
    """Patch `tokenscope.ccusage._run_json` to serve fixture JSON.

    Returns a callable used to register `argv-prefix → response` mappings.
    The prefix is matched against the start of the argv passed to
    `_run_json` (the most-specific prefix wins). The response can be a
    dict (literal) or a `str | Path` pointing at a JSON fixture file.

    Streamlit's `@st.cache_data` is cleared at fixture setup so tests
    that share the Python process don't see each other's cached responses.
    """
    responses: list[tuple[tuple[str, ...], dict]] = []

    def register(*argv_prefix: str, response: Any) -> None:
        """Register an `argv-prefix → response` pair. `response` accepts a
        dict (literal), a `str | Path` pointing at a JSON fixture, or a
        bare list / None — the production `_coerce_empty` is exercised
        for non-dict shapes, so tests can simulate ccusage's bare-`[]`
        empty-range response."""
        if isinstance(response, (str, Path)):
            response = json.loads(Path(response).read_text())
        responses.append((tuple(argv_prefix), response))

    def fake_run_json(args: list[str]) -> dict:
        # Most-specific prefix wins so callers can override a generic
        # `daily` mock with `daily --instances` etc.
        for prefix, response in sorted(
            responses, key=lambda pair: len(pair[0]), reverse=True
        ):
            if tuple(args[: len(prefix)]) == prefix:
                return response
        return _default_empty_response(args)

    monkeypatch.setattr("tokenscope.ccusage._run_json", fake_run_json)

    # Streamlit's cache_data persists across tests in the same process. Clear
    # it so each test sees a clean slate.
    try:
        import streamlit as st

        st.cache_data.clear()
    except Exception:  # pragma: no cover — streamlit always importable in dev
        pass

    return register


@pytest.fixture
def mock_ccusage_version(monkeypatch):
    """Patch the version helper too — the sidebar footer calls it."""
    monkeypatch.setattr(
        "tokenscope.ccusage.get_ccusage_version", lambda: "18.0.11"
    )
