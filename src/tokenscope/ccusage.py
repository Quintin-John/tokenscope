"""Subprocess wrapper around the locally-installed `ccusage` CLI.

ccusage is pinned in the sibling `package.json` and installed via `npm ci`
into `node_modules/.bin/ccusage`. This module shells out to that binary with
strict argument-list invocation (never `shell=True`, never via `npx`).
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from tokenscope.models import (
    BlocksReport,
    DailyReport,
    MonthlyReport,
    SessionReport,
    WeeklyReport,
)

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
    """Run ccusage with the given args and parse stdout as JSON."""
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
        raise CcusageError(f"ccusage produced invalid JSON: {exc}") from exc


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


def _date_args(since: str | None, until: str | None) -> list[str]:
    args: list[str] = []
    if since:
        args += ["--since", since]
    if until:
        args += ["--until", until]
    return args


def daily(since: str | None = None, until: str | None = None) -> DailyReport:
    return DailyReport.model_validate(_run_json(["daily", *_date_args(since, until)]))


def weekly(since: str | None = None, until: str | None = None) -> WeeklyReport:
    return WeeklyReport.model_validate(_run_json(["weekly", *_date_args(since, until)]))


def monthly(since: str | None = None, until: str | None = None) -> MonthlyReport:
    return MonthlyReport.model_validate(_run_json(["monthly", *_date_args(since, until)]))


def session(project: str | None = None) -> SessionReport:
    args: list[str] = []
    if project:
        args += ["--project", project]
    return SessionReport.model_validate(_run_json(["session", *args]))


def blocks(active: bool = False) -> BlocksReport:
    args: list[str] = []
    if active:
        args.append("--active")
    return BlocksReport.model_validate(_run_json(["blocks", *args]))
