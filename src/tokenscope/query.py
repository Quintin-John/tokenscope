"""Typed query object built by the sidebar and consumed by the data layer.

`Query` is a frozen dataclass — immutable and hashable so Streamlit's
`@st.cache_data` can key on it directly. All fields default to `None` so a
bare `Query()` means "no filters".
"""

from __future__ import annotations

from dataclasses import dataclass


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
