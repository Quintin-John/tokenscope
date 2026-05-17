"""Subprocess wrapper around the locally-installed `ccusage` CLI.

ccusage is pinned in the sibling `package.json` and installed via `npm ci`
into `node_modules/.bin/ccusage`. This module shells out to that binary with
strict argument-list invocation (never `shell=True`, never via `npx`).

This module is intentionally Streamlit-free. The caching layer that wraps
these functions with `@st.cache_data(ttl=30)` lives in `tokenscope.data`.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from tokenscope.models import (
    BlocksReport,
    DailyByProjectReport,
    DailyReport,
    MonthlyByProjectReport,
    MonthlyReport,
    SessionReport,
    WeeklyByProjectReport,
    WeeklyReport,
)
from tokenscope.query import Query

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CCUSAGE_BIN = REPO_ROOT / "node_modules" / ".bin" / "ccusage"


class CcusageError(RuntimeError):
    """Raised when ccusage fails to execute or returns malformed output."""


def _check_installed() -> Path:
    if not CCUSAGE_BIN.exists():
        raise CcusageError(
            f"ccusage binary not found at {CCUSAGE_BIN}. "
            f"Run `npm ci` (or `./scripts/setup.sh`) in {REPO_ROOT}."
        )
    return CCUSAGE_BIN


def _run_json(args: list[str]) -> dict[str, Any]:
    """Run ccusage with the given args and parse stdout as JSON.

    On JSON decode failure, the wrapped CcusageError includes the
    invoked argv, a snippet of stdout, and stderr — so a user-facing
    "ccusage failed" message points at the real cause (ccusage printing
    a non-JSON usage/error message to stdout, for example) instead of
    just the Python-side parse error.
    """
    binary = _check_installed()
    cmd = [str(binary), *args, "--json"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CcusageError(
            f"ccusage exited with code {exc.returncode}: {exc.stderr.strip()}"
        ) from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CcusageError(
            f"ccusage produced invalid JSON: {exc}\n"
            f"argv: {args}\n"
            f"stdout (first 300 chars): {result.stdout[:300]!r}\n"
            f"stderr (first 300 chars): {result.stderr[:300]!r}"
        ) from exc


@lru_cache(maxsize=1)
def get_ccusage_version() -> str:
    """Return the version string reported by `ccusage --version`."""
    binary = _check_installed()
    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _q(query: Query | None) -> list[str]:
    return query.to_args() if query is not None else []


_EMPTY_TOTALS = {
    "inputTokens": 0,
    "outputTokens": 0,
    "cacheCreationTokens": 0,
    "cacheReadTokens": 0,
    "totalTokens": 0,
    "totalCost": 0,
}


def _coerce_empty(raw, key: str, *, container_type=list):
    """ccusage emits a bare ``[]`` instead of the expected
    ``{"<key>": [], "totals": {...}}`` dict when a daily / session /
    daily --instances query returns no entries (empty range, project
    with no activity in window, etc.). Normalise it so pydantic
    validation doesn't crash with a misleading "model_type" error.
    """
    if raw is None or raw == [] or raw == {}:
        return {key: container_type(), "totals": dict(_EMPTY_TOTALS)}
    return raw


def daily(query: Query | None = None) -> DailyReport:
    raw = _coerce_empty(_run_json(["daily", *_q(query)]), "daily")
    return DailyReport.model_validate(raw)


def weekly(query: Query | None = None) -> WeeklyReport:
    raw = _coerce_empty(_run_json(["weekly", *_q(query)]), "weekly")
    return WeeklyReport.model_validate(raw)


def monthly(query: Query | None = None) -> MonthlyReport:
    raw = _coerce_empty(_run_json(["monthly", *_q(query)]), "monthly")
    return MonthlyReport.model_validate(raw)


def session(query: Query | None = None) -> SessionReport:
    raw = _coerce_empty(_run_json(["session", *_q(query)]), "sessions")
    return SessionReport.model_validate(raw)


def blocks(active: bool = False, query: Query | None = None) -> BlocksReport:
    args: list[str] = []
    if active:
        args.append("--active")
    args += _q(query)
    # blocks already returns a proper `{"blocks": [], "message": "..."}`
    # shape for empty ranges, so no coercion needed.
    return BlocksReport.model_validate(_run_json(["blocks", *args]))


def daily_by_project(query: Query | None = None) -> DailyByProjectReport:
    raw = _coerce_empty(
        _run_json(["daily", "--instances", *_q(query)]),
        "projects",
        container_type=dict,
    )
    return DailyByProjectReport.model_validate(raw)


def weekly_by_project(query: Query | None = None) -> WeeklyByProjectReport:
    raw = _coerce_empty(
        _run_json(["weekly", "--instances", *_q(query)]),
        "projects",
        container_type=dict,
    )
    return WeeklyByProjectReport.model_validate(raw)


def monthly_by_project(query: Query | None = None) -> MonthlyByProjectReport:
    raw = _coerce_empty(
        _run_json(["monthly", "--instances", *_q(query)]),
        "projects",
        container_type=dict,
    )
    return MonthlyByProjectReport.model_validate(raw)
