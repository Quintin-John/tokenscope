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

from tokenscope.models import BlocksReport, DailyReport
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
    """Uncached daily report. Used only by the live ccusage integration
    tests; production code goes through `tokenscope.data.daily` for the
    Streamlit cache layer."""
    raw = _coerce_empty(_run_json(["daily", *Query.argv(query)]), "daily")
    return DailyReport.model_validate(raw)


def blocks(active: bool = False, query: Query | None = None) -> BlocksReport:
    """Uncached blocks report. Used only by the live ccusage integration
    tests; production code goes through `tokenscope.data.blocks`.

    `blocks` already returns a proper `{"blocks": [], "message": "..."}`
    shape for empty ranges, so no _coerce_empty wrapping is needed.
    """
    args: list[str] = []
    if active:
        args.append("--active")
    args.extend(Query.argv(query))
    return BlocksReport.model_validate(_run_json(["blocks", *args]))
