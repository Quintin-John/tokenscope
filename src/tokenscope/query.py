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

    def to_args(self) -> list[str]:
        """Render the query as a ccusage argv slice (no command name)."""
        args: list[str] = []
        if self.since:
            args += ["--since", self.since]
        if self.until:
            args += ["--until", self.until]
        if self.project:
            args += ["--project", self.project]
        if self.offline:
            args.append("--offline")
        return args
