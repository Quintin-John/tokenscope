"""Tests for tokenscope.plans — the sidebar Plan selector metadata."""

from __future__ import annotations

import pytest

from tokenscope.plans import DEFAULT_PLAN, PLANS, get_plan, plan_names


def test_plan_names_order() -> None:
    # Enterprise must be first so it's the default selectbox option (PLAN §3.3).
    assert plan_names() == ["Enterprise", "Pro", "Max 5×", "Max 20×"]


def test_default_plan_is_first_and_pay_as_you_go() -> None:
    # DEFAULT_PLAN is the single authority for "which plan is the default":
    # the first entry, pay-as-you-go (no flat-rate banner). The sidebar's
    # selectbox index and URL-omission both derive from it.
    assert DEFAULT_PLAN is PLANS[0]
    assert DEFAULT_PLAN.name == "Enterprise"
    assert DEFAULT_PLAN.is_flat_rate is False


def test_enterprise_is_pay_as_you_go() -> None:
    plan = get_plan("Enterprise")
    assert plan.flat_rate_usd_per_month is None
    assert plan.is_flat_rate is False
    assert plan.banner_text() is None


@pytest.mark.parametrize(
    "name,rate",
    [("Pro", 20.0), ("Max 5×", 100.0), ("Max 20×", 200.0)],
)
def test_flat_rate_plans_have_banner(name: str, rate: float) -> None:
    plan = get_plan(name)
    assert plan.flat_rate_usd_per_month == rate
    assert plan.is_flat_rate is True
    text = plan.banner_text()
    assert text is not None
    assert f"${rate:.0f}/month" in text
    assert "API-equivalent cost" in text


def test_get_plan_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown plan"):
        get_plan("Free Trial")


def test_plans_are_hashable_frozen() -> None:
    # Frozen dataclass → hashable, so we could use plans as @st.cache_data keys.
    {p: p.name for p in PLANS}
