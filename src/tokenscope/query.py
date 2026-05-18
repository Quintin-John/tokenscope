"""Typed query object built by the sidebar and consumed by the data layer.

`Query` is a frozen dataclass — immutable and hashable so Streamlit's
`@st.cache_data` can key on it directly. All fields default to `None` so a
bare `Query()` means "no filters".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Query:
    since: str | None = None
    until: str | None = None
    project: str | None = None
    offline: bool = False
    tz: str | None = None  # IANA timezone, e.g. "America/Los_Angeles"

    def to_args(self) -> list[str]:
        """Render the query as a ccusage argv slice (no command name).

        Uses the `--key=value` joined form rather than two separate argv
        entries. ccusage's CLI parser treats a value beginning with `-`
        as the next flag when args are space-separated, which breaks
        every project id (they're slugified absolute paths like
        `-Users-quintin-…`). The joined form is unambiguous.
        """
        args: list[str] = []
        if self.since:
            args.append(f"--since={self.since}")
        if self.until:
            args.append(f"--until={self.until}")
        if self.project:
            args.append(f"--project={self.project}")
        if self.tz:
            args.append(f"--timezone={self.tz}")
        if self.offline:
            args.append("--offline")
        return args

    @staticmethod
    def argv(query: "Query | None") -> list[str]:
        """Null-safe variant of `to_args()`. Replaces the two identical
        one-line helpers (`ccusage._q`, `data._q_args`) that previously
        wrapped this conditional. A bare ``Query()`` already means "no
        filters" (every field defaults to None), so `Query.argv(q)` is
        the single authoritative way to build the argv slice when the
        caller may have been handed `None`."""
        return query.to_args() if query is not None else []

    # --- bound parsing -----------------------------------------------------
    #
    # `since` and `until` are stored as ccusage's compact `YYYYMMDD` strings
    # (that's what ccusage's CLI parser accepts). Three independent parsers
    # used to re-decode them — `analytics.prior_window_query`,
    # `overview._window_days`, `cache._parse_ccusage_date` — each with its
    # own try/except shape. These methods are now the one authoritative
    # decoder. Strict YYYYMMDD only: the sidebar formats bounds via
    # `_to_ccusage_date(date) -> str` which always emits 8-digit form, and
    # URL-driven `since`/`until` are re-formatted before reaching `Query`,
    # so the input is invariant.

    def since_date(self) -> date | None:
        """Parse `self.since` as a `date`. Returns `None` when `since`
        is missing or malformed."""
        return _parse_yyyymmdd(self.since)

    def until_date(self) -> date | None:
        """Parse `self.until` as a `date`. Returns `None` when `until`
        is missing or malformed."""
        return _parse_yyyymmdd(self.until)

    def window_days(self) -> int | None:
        """Inclusive day count of the window (`since` and `until`
        inclusive on both ends).

        A 30-day window (`since == today - 29, until == today`) returns
        30. A single-day window (`since == until`) returns 1.

        Returns `None` when either bound is missing or malformed —
        callers fall back to `config.DEFAULT_RANGE_DAYS`.
        """
        since = self.since_date()
        until = self.until_date()
        if since is None or until is None:
            return None
        return (until - since).days + 1


def _parse_yyyymmdd(raw: str | None) -> date | None:
    """Parse a ccusage-format `YYYYMMDD` string into a `date`.

    Returns `None` for missing / malformed input — every caller treats
    `None` as "no bound set, fall back to default" rather than raising.

    Strict format: exactly 8 digits, parsed via
    `datetime.strptime("%Y%m%d")`. The explicit length / digit guard is
    load-bearing — `strptime` accepts variable-width `%m` / `%d`, so
    without it `"2026041"` would parse as April 1 (7 chars: 4-digit
    year + 1-digit month + 2-digit day), silently misinterpreting a
    malformed input as a valid date. The pre-Slice-D parsers in
    `analytics.prior_window_query` and `overview._window_days` had
    exactly that quirk; `cache._parse_ccusage_date` had a strict
    length check that did NOT. Slice D picks the stricter contract
    (matching the cache.py original) so the failure mode for forged
    or drifted input is "fall back to default" rather than "compute
    against a garbage date".

    Production input is always 8-digit (`sidebar._to_ccusage_date`
    emits `%Y%m%d` exclusively, URL `since`/`until` are reformatted
    before reaching `Query`), so this stricter contract is invisible
    to valid call sites.

    Module-level so both `since_date` and `until_date` share one parser
    without re-paying the inner-function definition on every call.
    """
    if not raw:
        return None
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None
