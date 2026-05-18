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


# --- Builder filter logic (pre-Slice E regression pins) -----------------
#
# `_build_family_rates` and `_build_model_rates` each walk
# `pricing_data.items()` and apply the SAME four filters:
#
#   1. `not isinstance(info, dict)`     — defensive against bad schema
#   2. `not model_id.startswith("claude-")` — Anthropic-only
#   3. `"/" in model_id`                — skip bedrock/vertex aliases
#   4. `model_id.endswith(":beta")`     — skip beta variants
#
# Plus `_build_family_rates` takes a median across the surviving
# versions in a family. None of the four filters or the median path
# is directly tested today — the existing test fixture has one entry
# per family with no aliases, so the filter chain never sees a value
# it must reject.
#
# An upcoming slice will unify the two builders into a single pass
# yielding `(model_id, family, FamilyRates)` tuples. These tests pin
# the filter contract so any drift between the pre-unification
# behaviour and the post-unification single-pass behaviour fails loudly.


def test_build_rates_skip_non_claude_model_ids() -> None:
    """LiteLLM's pricing schema carries non-Anthropic entries
    (`gpt-4o`, `gemini-pro`, etc.). Both builders must skip
    anything whose id doesn't start with `claude-` — otherwise a
    `gpt-4o` entry would contribute to the `gpt-4o` "family"
    (since `model_family` passes non-claude names through verbatim)
    and a model-rates lookup for a Claude id could surface
    OpenAI pricing on the fallback path."""
    data = {
        "claude-opus-4-7": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 25e-6,
            "cache_creation_input_token_cost": 6.25e-6,
            "cache_read_input_token_cost": 0.5e-6,
        },
        "gpt-4o": {
            "input_cost_per_token": 2.5e-6,
            "output_cost_per_token": 10e-6,
        },
        "gemini-1.5-pro": {
            "input_cost_per_token": 1.25e-6,
            "output_cost_per_token": 5e-6,
        },
    }
    family_rates = pricing._build_family_rates(data)
    model_rates = pricing._build_model_rates(data)
    assert set(family_rates) == {"opus"}, (
        f"non-claude families leaked into family rates: {family_rates!r}"
    )
    assert set(model_rates) == {"claude-opus-4-7"}, (
        f"non-claude ids leaked into model rates: {set(model_rates)!r}"
    )


def test_build_rates_skip_bedrock_and_vertex_aliases() -> None:
    """LiteLLM also carries Claude-via-Bedrock and Claude-via-Vertex
    aliases (`bedrock/claude-opus-4-7`, `vertex_ai/claude-opus-4-7`)
    with different pricing. The slash discriminator skips them so
    only direct-Anthropic-API rates feed the median.

    Without the filter, a vertex entry at $50/M input would shift
    the opus family median dramatically — the dashboard's "Estimate
    accuracy: ±X%" caption would diverge from ccusage's actual
    costs by an entire pricing tier."""
    data = {
        "claude-opus-4-7": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 25e-6,
            "cache_creation_input_token_cost": 6.25e-6,
            "cache_read_input_token_cost": 0.5e-6,
        },
        "bedrock/claude-opus-4-7": {
            "input_cost_per_token": 50e-6,  # would skew the median
            "output_cost_per_token": 250e-6,
            "cache_creation_input_token_cost": 62.5e-6,
            "cache_read_input_token_cost": 5.0e-6,
        },
        "vertex_ai/claude-opus-4-7": {
            "input_cost_per_token": 100e-6,
            "output_cost_per_token": 500e-6,
            "cache_creation_input_token_cost": 125e-6,
            "cache_read_input_token_cost": 10e-6,
        },
    }
    family_rates = pricing._build_family_rates(data)
    model_rates = pricing._build_model_rates(data)
    assert family_rates["opus"]["input"] == pytest.approx(5.0), (
        "bedrock/vertex aliases polluted the opus family median: "
        f"input={family_rates['opus']['input']!r}, expected 5.0"
    )
    assert "bedrock/claude-opus-4-7" not in model_rates
    assert "vertex_ai/claude-opus-4-7" not in model_rates


def test_build_rates_skip_beta_suffixed_ids() -> None:
    """Beta-tagged entries (`claude-opus-4-7:beta`) are often
    promotional discounts or pre-release placeholders with rates
    that don't reflect what an Enterprise customer will be billed.
    Both builders skip them."""
    data = {
        "claude-opus-4-7": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 25e-6,
            "cache_creation_input_token_cost": 6.25e-6,
            "cache_read_input_token_cost": 0.5e-6,
        },
        "claude-opus-4-7:beta": {
            "input_cost_per_token": 1e-6,  # promotional, would skew
            "output_cost_per_token": 5e-6,
            "cache_creation_input_token_cost": 1.25e-6,
            "cache_read_input_token_cost": 0.1e-6,
        },
    }
    family_rates = pricing._build_family_rates(data)
    model_rates = pricing._build_model_rates(data)
    assert family_rates["opus"]["input"] == pytest.approx(5.0), (
        f"beta variant polluted the opus median: {family_rates!r}"
    )
    assert "claude-opus-4-7:beta" not in model_rates


def test_build_rates_skip_non_dict_info_defensively() -> None:
    """LiteLLM's JSON occasionally carries top-level keys whose value
    isn't a model-info dict (`"sample_spec": "..."`, future
    schema-version markers, etc.). Both builders must skip them
    rather than crash on `info[key]` access.

    Catching this here guards the unification slice: a single-pass
    rewrite that forgets the `isinstance(info, dict)` guard would
    raise `TypeError` on the first non-dict value."""
    data = {
        "claude-opus-4-7": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 25e-6,
            "cache_creation_input_token_cost": 6.25e-6,
            "cache_read_input_token_cost": 0.5e-6,
        },
        "sample_spec": "this is a meta-key, not a model info dict",
        "_schema_version": 2,
        "deprecated_marker": None,
    }
    # Must not raise.
    family_rates = pricing._build_family_rates(data)
    model_rates = pricing._build_model_rates(data)
    assert set(family_rates) == {"opus"}
    assert set(model_rates) == {"claude-opus-4-7"}


def test_build_family_rates_uses_median_across_versions() -> None:
    """Multiple versions in the same family — median is the
    aggregation, not mean. Median is robust to one anomalous entry
    (e.g. a deprecated discounted version) pulling the family rate
    sideways.

    Three opus versions: input rates 3, 5, 7 → median 5.
    Output: 20, 25, 30 → median 25."""
    data = {
        "claude-opus-4-5": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 20e-6,
            "cache_creation_input_token_cost": 3.75e-6,
            "cache_read_input_token_cost": 0.3e-6,
        },
        "claude-opus-4-6": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 25e-6,
            "cache_creation_input_token_cost": 6.25e-6,
            "cache_read_input_token_cost": 0.5e-6,
        },
        "claude-opus-4-7": {
            "input_cost_per_token": 7e-6,
            "output_cost_per_token": 30e-6,
            "cache_creation_input_token_cost": 8.75e-6,
            "cache_read_input_token_cost": 0.7e-6,
        },
    }
    family_rates = pricing._build_family_rates(data)
    assert family_rates["opus"]["input"] == pytest.approx(5.0)
    assert family_rates["opus"]["output"] == pytest.approx(25.0)
    assert family_rates["opus"]["cache_create"] == pytest.approx(6.25)
    assert family_rates["opus"]["cache_read"] == pytest.approx(0.5)


def test_build_rates_treat_missing_field_as_zero() -> None:
    """A model entry that's missing one of the four cost fields
    (e.g. a legacy entry from before cache pricing was published)
    must produce a `0.0` rate for the missing kind, not crash, not
    `None`. Downstream consumers (`cost_by_kind`, `cache_savings`)
    compute `cost = tokens * rate / 1M` — None would `TypeError`,
    zero correctly contributes nothing to the estimate.

    For `_build_family_rates`, the median is taken over the entries
    that DO have the key — a single-version family where the only
    entry lacks the key still yields 0.0 (empty `vals` list)."""
    data = {
        "claude-opus-4-7": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 25e-6,
            # cache_creation + cache_read intentionally missing
        },
    }
    family_rates = pricing._build_family_rates(data)
    model_rates = pricing._build_model_rates(data)

    assert family_rates["opus"]["input"] == pytest.approx(5.0)
    assert family_rates["opus"]["output"] == pytest.approx(25.0)
    assert family_rates["opus"]["cache_create"] == 0.0
    assert family_rates["opus"]["cache_read"] == 0.0

    assert model_rates["claude-opus-4-7"]["input"] == pytest.approx(5.0)
    assert model_rates["claude-opus-4-7"]["cache_create"] == 0.0
    assert model_rates["claude-opus-4-7"]["cache_read"] == 0.0
