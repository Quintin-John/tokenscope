"""Subscription plan metadata for the sidebar Plan selector.

Per PLAN.md §3.3, this is **pure labelling** — selecting a plan never alters
token counts, cache stats, or any cost figure. The plan only controls
whether the overview shows an "API-equivalent cost" banner explaining
that displayed costs are what you'd pay at API rates, not what your
flat-rate subscription actually bills.

Flat-rate prices are taken from Anthropic's public pricing as of
2026-05-16. They live here (not on Anthropic's side of the wire) so bumping
them is a one-line edit when prices move.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Plan:
    name: str
    flat_rate_usd_per_month: float | None  # None = pay-as-you-go (Enterprise/API)

    @property
    def is_flat_rate(self) -> bool:
        return self.flat_rate_usd_per_month is not None

    def banner_text(self) -> str | None:
        """The API-equivalent banner text, or None for pay-as-you-go plans."""
        if not self.is_flat_rate:
            return None
        return (
            f"Showing API-equivalent cost — your plan is flat-rate at "
            f"${self.flat_rate_usd_per_month:.0f}/month."
        )


# Enterprise is first so it's the default selectbox option (PLAN.md §3.3).
PLANS: tuple[Plan, ...] = (
    Plan(name="Enterprise", flat_rate_usd_per_month=None),
    Plan(name="Pro", flat_rate_usd_per_month=20.0),
    Plan(name="Max 5×", flat_rate_usd_per_month=100.0),
    Plan(name="Max 20×", flat_rate_usd_per_month=200.0),
)


# Authoritative default plan. The sidebar selects this on first load and
# omits it from shareable URLs to keep links short (PLAN.md §3.3). Both
# the selectbox default-index and the URL-omission check derive from this
# constant, so changing the default — or reordering PLANS — is a one-line
# edit here rather than scattered name/index literals at the call sites.
DEFAULT_PLAN: Plan = PLANS[0]


def get_plan(name: str) -> Plan:
    for plan in PLANS:
        if plan.name == name:
            return plan
    raise ValueError(f"Unknown plan: {name!r}")


def plan_names() -> list[str]:
    return [p.name for p in PLANS]
