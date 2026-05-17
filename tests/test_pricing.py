"""Unit tests for tokenscope.pricing.

Focus: the cache-loading invariant that `_ensure_loaded()` is atomic —
either all three module caches (`_PRICING_DATA_CACHE`,
`_FAMILY_RATES_CACHE`, `_MODEL_RATES_CACHE`) get populated together, or
none of them do. The pre-fix code mutated `_PRICING_DATA_CACHE` first
and then built the two rate dicts; if either build raised, the data
cache was left set and subsequent calls would short-circuit
`_ensure_loaded` and trip the post-load asserts in `rates_for_model` /
`rates_for_family`.

Network and disk are stubbed via monkeypatch on `_fetch_pricing_json`
so the tests are deterministic and don't touch `~/.cache/tokenscope`.
"""

from __future__ import annotations

import pytest

from tokenscope import pricing


def _minimal_pricing_data() -> dict:
    """A LiteLLM-shaped pricing dict with one entry per family. Enough
    for `_build_family_rates` / `_build_model_rates` to populate the
    caches; not realistic, but well-formed."""
    return {
        "claude-opus-4-7": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 25e-6,
            "cache_creation_input_token_cost": 6.25e-6,
            "cache_read_input_token_cost": 0.5e-6,
        },
        "claude-sonnet-4-6": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 15e-6,
            "cache_creation_input_token_cost": 3.75e-6,
            "cache_read_input_token_cost": 0.3e-6,
        },
        "claude-haiku-4-5-20251001": {
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 5e-6,
            "cache_creation_input_token_cost": 1.25e-6,
            "cache_read_input_token_cost": 0.1e-6,
        },
    }


@pytest.fixture(autouse=True)
def _isolate_pricing_caches():
    """Every test starts with empty caches; clean up after so a failure
    doesn't bleed state into the next test."""
    pricing.reset_cache()
    yield
    pricing.reset_cache()


def test_ensure_loaded_populates_all_three_caches(monkeypatch) -> None:
    """Happy path: a successful fetch populates the three caches together
    and a subsequent call returns True without re-fetching."""
    fetch_calls = {"n": 0}

    def _fake_fetch() -> dict:
        fetch_calls["n"] += 1
        return _minimal_pricing_data()

    monkeypatch.setattr(pricing, "_fetch_pricing_json", _fake_fetch)

    assert pricing._ensure_loaded() is True
    assert pricing._PRICING_DATA_CACHE is not None
    assert pricing._FAMILY_RATES_CACHE is not None
    assert pricing._MODEL_RATES_CACHE is not None

    # Second call: short-circuits via the `_PRICING_DATA_CACHE is not None`
    # guard, no second fetch.
    assert pricing._ensure_loaded() is True
    assert fetch_calls["n"] == 1


def test_ensure_loaded_returns_false_when_fetch_returns_none(monkeypatch) -> None:
    """Network failure with no cached fallback: every cache stays None,
    callers see False and surface "rates unavailable" to the UI."""
    monkeypatch.setattr(pricing, "_fetch_pricing_json", lambda: None)

    assert pricing._ensure_loaded() is False
    assert pricing._PRICING_DATA_CACHE is None
    assert pricing._FAMILY_RATES_CACHE is None
    assert pricing._MODEL_RATES_CACHE is None


def test_ensure_loaded_is_atomic_when_family_build_raises(monkeypatch) -> None:
    """The whole point of this slice.

    Before the fix, `_PRICING_DATA_CACHE = data` ran BEFORE
    `_build_family_rates(data)`. If the family-build raised on the first
    call, the data cache was already set; a subsequent call saw it
    non-None, short-circuited, and the `assert _MODEL_RATES_CACHE is
    not None` in `rates_for_model` blew up.

    Post-fix: a raising builder leaves all three caches None. The
    exception propagates so the caller sees the bug rather than a
    silent half-loaded state.
    """
    monkeypatch.setattr(pricing, "_fetch_pricing_json", _minimal_pricing_data)

    def _raise(_data: dict) -> dict:
        raise RuntimeError("simulated malformed pricing schema")

    monkeypatch.setattr(pricing, "_build_family_rates", _raise)

    with pytest.raises(RuntimeError, match="malformed pricing schema"):
        pricing._ensure_loaded()

    # Atomicity invariant: no cache was mutated. This assertion is what
    # would have failed on the pre-fix code — `_PRICING_DATA_CACHE`
    # would be the fixture dict, not None.
    assert pricing._PRICING_DATA_CACHE is None, (
        "data cache leaked despite family-build failure — atomicity broken"
    )
    assert pricing._FAMILY_RATES_CACHE is None
    assert pricing._MODEL_RATES_CACHE is None


def test_ensure_loaded_is_atomic_when_model_build_raises(monkeypatch) -> None:
    """Same invariant on the `_build_model_rates` failure path —
    family-build succeeded but model-build raised. Pre-fix this also
    left the data cache populated; post-fix all three stay None."""
    monkeypatch.setattr(pricing, "_fetch_pricing_json", _minimal_pricing_data)

    def _raise(_data: dict) -> dict:
        raise ValueError("simulated bad model entry")

    monkeypatch.setattr(pricing, "_build_model_rates", _raise)

    with pytest.raises(ValueError, match="bad model entry"):
        pricing._ensure_loaded()

    assert pricing._PRICING_DATA_CACHE is None
    assert pricing._FAMILY_RATES_CACHE is None
    assert pricing._MODEL_RATES_CACHE is None


def test_rates_for_model_returns_exact_match_when_present(monkeypatch) -> None:
    """End-to-end: with caches populated, an exact model id hits the
    per-model dict — not the family fallback."""
    monkeypatch.setattr(pricing, "_fetch_pricing_json", _minimal_pricing_data)
    rates = pricing.rates_for_model("claude-opus-4-7")
    assert rates is not None
    assert rates["output"] == pytest.approx(25.0)  # 25e-6 * 1M


def test_rates_for_model_falls_back_to_family_for_unknown_id(monkeypatch) -> None:
    """An unknown exact id (e.g. a not-yet-published variant) falls back
    to the family's median rates. Verifies the assert at the bottom of
    rates_for_model doesn't fire on the fallback path."""
    monkeypatch.setattr(pricing, "_fetch_pricing_json", _minimal_pricing_data)
    rates = pricing.rates_for_model("claude-opus-4-99-future")
    assert rates is not None
    # Single opus entry in the fixture → median == that entry's rates.
    assert rates["input"] == pytest.approx(5.0)


def test_rates_for_family_returns_none_when_unavailable(monkeypatch) -> None:
    """No pricing data → `rates_for_family` returns None rather than
    raising. UI relies on this to hide the Cost-composition panel."""
    monkeypatch.setattr(pricing, "_fetch_pricing_json", lambda: None)
    assert pricing.rates_for_family("opus") is None


def test_rates_for_model_returns_none_when_unavailable(monkeypatch) -> None:
    """Same contract for the per-model lookup."""
    monkeypatch.setattr(pricing, "_fetch_pricing_json", lambda: None)
    assert pricing.rates_for_model("claude-opus-4-7") is None
