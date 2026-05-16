"""URL-state model for the dashboard's drill-down view.

The current view (overview / day / session / block) and any drill targets
(date, sessionId, blockId) are mirrored into `st.query_params` so URLs
are shareable and the browser's back button works (PLAN.md §3.3).

This module is intentionally Streamlit-free: it parses an already-fetched
params mapping and produces another. The UI layer is responsible for
reading from / writing to `st.query_params`. Keeping it pure means the
routing logic is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

ViewName = Literal[
    "overview", "day", "session", "block", "cache", "models", "live"
]
VALID_VIEWS: tuple[ViewName, ...] = (
    "overview",
    "day",
    "session",
    "block",
    "cache",
    "models",
    "live",
)
TOP_LEVEL_VIEWS: tuple[ViewName, ...] = ("overview", "live", "cache", "models")


@dataclass(frozen=True, slots=True)
class Navigation:
    view: ViewName = "overview"
    day: str | None = None
    session: str | None = None
    block: str | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> "Navigation":
        """Build navigation state from a (possibly hostile) query-params mapping.

        Unknown / malformed `view` values fall back to "overview" rather
        than raising, so a tampered URL can't crash the app.
        """
        view_raw = params.get("view", "overview")
        view: ViewName = view_raw if view_raw in VALID_VIEWS else "overview"  # type: ignore[assignment]
        return cls(
            view=view,
            day=params.get("day") or None,
            session=params.get("session") or None,
            block=params.get("block") or None,
        )

    def to_params(self) -> dict[str, str]:
        """Serialize back to a flat query-params dict. Omits empty fields."""
        out: dict[str, str] = {"view": self.view}
        if self.day:
            out["day"] = self.day
        if self.session:
            out["session"] = self.session
        if self.block:
            out["block"] = self.block
        return out

    # ---- transitions ----

    def to_overview(self) -> "Navigation":
        return Navigation(view="overview")

    def to_day(self, day: str) -> "Navigation":
        return Navigation(view="day", day=day)

    def to_session(self, session_id: str) -> "Navigation":
        return Navigation(view="session", day=self.day, session=session_id)

    def to_block(self, block_id: str) -> "Navigation":
        return Navigation(
            view="block", day=self.day, session=self.session, block=block_id
        )

    # ---- breadcrumb trail ----

    def trail(self) -> list[tuple[str, "Navigation"]]:
        """Crumbs from root to here as (label, target) pairs."""
        crumbs: list[tuple[str, Navigation]] = [("Overview", Navigation(view="overview"))]
        if self.view == "overview":
            return crumbs
        if self.day:
            crumbs.append((self.day, Navigation(view="day", day=self.day)))
        if self.view == "session" and self.session:
            crumbs.append((_short(self.session), Navigation(view="session", day=self.day, session=self.session)))
        if self.view == "block" and self.block:
            if self.session:
                crumbs.append((_short(self.session), Navigation(view="session", day=self.day, session=self.session)))
            crumbs.append((_short(self.block), self))
        return crumbs


def _short(identifier: str, length: int = 24) -> str:
    """Truncate session / block identifiers for breadcrumb display."""
    if len(identifier) <= length:
        return identifier
    return identifier[: length - 1] + "…"
