"""Per-family, per-kind Anthropic pricing — fetched live from LiteLLM.

ccusage uses LiteLLM's pricing schema internally (`raw.githubusercontent
.com/BerriAI/litellm/main/model_prices_and_context_window.json`). We
fetch from the same source so the Overview's "Cost composition" panel
uses the same numbers ccusage does — no hardcoded rates that go stale.

Caching:
- Successful fetches are cached under `~/.cache/tokenscope/litellm_pricing.json`
  with a 7-day TTL.
- Network failure falls back to the on-disk cache if any.
- No cache + no network → `rates_for_family` returns `None`; the
  Cost-composition UI hides itself rather than showing wrong numbers.

We deliberately don't depend on the `litellm` Python package — it's
~80 MB of transitive deps. A 5-line stdlib `urllib` fetch of one JSON
file is enough.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from statistics import median
from typing import Iterator, TypedDict

from tokenscope import config
from tokenscope.log import get_logger

_log = get_logger(__name__)

LITELLM_PRICING_URL = config.PRICING_LITELLM_URL
_CACHE_DIR = config.PRICING_CACHE_DIR
_CACHE_FILE = _CACHE_DIR / "litellm_pricing.json"
_CACHE_TTL_SECONDS = config.PRICING_CACHE_TTL_SECONDS
_FETCH_TIMEOUT_SECONDS = config.PRICING_FETCH_TIMEOUT_SECONDS


class FamilyRates(TypedDict):
    input: float
    output: float
    cache_create: float
    cache_read: float


KIND_INPUT = "input"
KIND_OUTPUT = "output"
KIND_CACHE_CREATE = "cache_create"
KIND_CACHE_READ = "cache_read"
KINDS: tuple[str, ...] = (KIND_INPUT, KIND_OUTPUT, KIND_CACHE_CREATE, KIND_CACHE_READ)


# In-process memoisation. Survives across `rates_for_*` calls but not across
# Python restarts (that's what the file cache is for).
_FAMILY_RATES_CACHE: dict[str, FamilyRates] | None = None
_MODEL_RATES_CACHE: dict[str, FamilyRates] | None = None
_PRICING_DATA_CACHE: dict | None = None


def _fetch_pricing_json() -> dict | None:
    """Return the LiteLLM pricing dict — from file cache if fresh, else
    from the network, else from stale cache, else None."""
    if _CACHE_FILE.exists():
        try:
            age = time.time() - _CACHE_FILE.stat().st_mtime
            if age < _CACHE_TTL_SECONDS:
                _log.debug("pricing.cache.fresh age_seconds=%d", int(age))
                return json.loads(_CACHE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    _log.info("pricing.fetch.start url=%s", LITELLM_PRICING_URL)
    fetch_start = time.monotonic()
    try:
        with urllib.request.urlopen(
            LITELLM_PRICING_URL, timeout=_FETCH_TIMEOUT_SECONDS
        ) as resp:
            raw = resp.read()
        data = json.loads(raw)
        duration_ms = int((time.monotonic() - fetch_start) * 1000)
        _log.info(
            "pricing.fetch.ok bytes=%d duration_ms=%d", len(raw), duration_ms
        )
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(json.dumps(data))
        except OSError:
            pass  # cache write is best-effort; failure isn't fatal.
        return data
    except (urllib.error.URLError, json.JSONDecodeError, OSError, TimeoutError) as exc:
        _log.warning(
            "pricing.fetch.failed exc=%s using_stale_cache=%s",
            exc,
            _CACHE_FILE.exists(),
        )

    # Stale cache as last resort.
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            _log.error("pricing.unavailable network_failed_and_cache_corrupt")
            return None
    _log.error("pricing.unavailable network_failed_and_no_cache")
    return None


def _per_token_to_per_mtok(per_token: float | None) -> float:
    return float(per_token) * 1_000_000 if per_token else 0.0


# Single source for the LiteLLM JSON-field-name → kind mapping. Both
# builders below resolve a kind's rate by looking up the LiteLLM key
# here. Adding a new kind to `pricing.KINDS` requires one new entry
# in this dict; a LiteLLM schema rename requires editing one value.
# Pre-Slice-E both builders re-listed these four strings inline.
_LITELLM_KEY_BY_KIND: dict[str, str] = {
    KIND_INPUT:        "input_cost_per_token",
    KIND_OUTPUT:       "output_cost_per_token",
    KIND_CACHE_CREATE: "cache_creation_input_token_cost",
    KIND_CACHE_READ:   "cache_read_input_token_cost",
}


def _iter_claude_pricing(
    pricing_data: dict,
) -> Iterator[tuple[str, str, dict]]:
    """Yield `(model_id, family, info_dict)` for every Claude entry in
    the LiteLLM pricing data that survives the shared filter chain.

    Pre-Slice-E both `_build_family_rates` and `_build_model_rates`
    re-applied this filter chain independently. Consolidating here
    means the filter contract has exactly one definition — adding a
    new prefix to skip (e.g. `sagemaker/`) is a one-line edit, not
    two.

    Filters (in order):
      1. `info` must be a dict — LiteLLM JSON occasionally carries
         meta-keys like `sample_spec` whose value is a string;
         silently skipping them keeps the loop crash-free.
      2. `model_id` must start with `claude-` — Anthropic-only.
      3. `model_id` must not contain `/` — skips bedrock / vertex /
         sagemaker aliases whose rates often differ by an order of
         magnitude and would skew family medians.
      4. `model_id` must not end with `:beta` — promotional /
         pre-release rates that don't reflect production billing.
    """
    # Local import to avoid the analytics ↔ pricing cycle at module
    # load time. `model_family` is a pure pattern match — no I/O.
    from tokenscope.analytics import model_family

    for model_id, info in pricing_data.items():
        if not isinstance(info, dict):
            continue
        if not model_id.startswith("claude-"):
            continue
        if "/" in model_id or model_id.endswith(":beta"):
            continue
        yield model_id, model_family(model_id), info


def _rates_from_info(info: dict) -> FamilyRates:
    """Convert a single LiteLLM `info` dict into per-MTok rates for
    every kind. Missing fields surface as `0.0` — downstream
    consumers compute `cost = tokens * rate / 1M`, where zero
    correctly contributes nothing to the estimate."""
    return FamilyRates(**{
        kind: _per_token_to_per_mtok(info.get(litellm_key))
        for kind, litellm_key in _LITELLM_KEY_BY_KIND.items()
    })


def _build_model_rates(pricing_data: dict) -> dict[str, FamilyRates]:
    """Per-model rate dictionary: exact LiteLLM key → rates.

    Lets the consumer hit the precise rate for `claude-opus-4-7` rather
    than averaging across every opus-* alias (the rate sometimes shifts
    significantly between version numbers).
    """
    return {
        model_id: _rates_from_info(info)
        for model_id, _, info in _iter_claude_pricing(pricing_data)
    }


def _build_family_rates(pricing_data: dict) -> dict[str, FamilyRates]:
    """Group Claude models in the LiteLLM schema by family, pick the
    median rate across versions within each family.

    Median is robust to one anomalous version (e.g. a deprecated
    discounted entry) pulling the family's rate sideways. Missing
    per-field values are EXCLUDED from the median — a legacy entry
    that lacks `cache_read_input_token_cost` shouldn't be counted
    as a 0.0 sample that would drag the family's cache_read median
    toward zero. The `e.get(key) is not None` guard preserves that
    contract; an empty `vals` list (every entry in the family lacks
    the key) collapses to 0.0, matching the per-entry default.
    """
    by_family: dict[str, list[dict]] = {}
    for _, family, info in _iter_claude_pricing(pricing_data):
        by_family.setdefault(family, []).append(info)

    rates: dict[str, FamilyRates] = {}
    for family, entries in by_family.items():
        per_kind: dict[str, float] = {}
        for kind, litellm_key in _LITELLM_KEY_BY_KIND.items():
            vals = [
                _per_token_to_per_mtok(e[litellm_key])
                for e in entries
                if e.get(litellm_key) is not None
            ]
            per_kind[kind] = float(median(vals)) if vals else 0.0
        rates[family] = FamilyRates(**per_kind)
    return rates


def _ensure_loaded() -> bool:
    """Populate the three module caches from `_fetch_pricing_json()`.

    Cache assignment is atomic: every derived dict is built locally
    before any of the three globals is mutated. If `_build_family_rates`
    or `_build_model_rates` raises on malformed pricing data, the
    exception propagates and the module is left in its pre-call state
    (all three caches still None). A retry hits `_fetch_pricing_json`
    fresh — there is no half-loaded state that would let the post-load
    `assert _MODEL_RATES_CACHE is not None` in `rates_for_model` fire.
    """
    global _PRICING_DATA_CACHE, _FAMILY_RATES_CACHE, _MODEL_RATES_CACHE
    if _PRICING_DATA_CACHE is not None:
        return True
    data = _fetch_pricing_json()
    if data is None:
        return False
    family_rates = _build_family_rates(data)
    model_rates = _build_model_rates(data)
    _PRICING_DATA_CACHE = data
    _FAMILY_RATES_CACHE = family_rates
    _MODEL_RATES_CACHE = model_rates
    return True


def rates_for_model(model_name: str) -> FamilyRates | None:
    """Per-MTok rates for an exact model name (e.g. `claude-opus-4-7`).

    Falls back to the family's median rates when LiteLLM doesn't carry
    the exact id (yet-to-be-published model, an aliased variant, etc.).
    Returns ``None`` when LiteLLM data isn't reachable at all.
    """
    if not _ensure_loaded():
        return None
    assert _MODEL_RATES_CACHE is not None and _FAMILY_RATES_CACHE is not None
    if model_name in _MODEL_RATES_CACHE:
        return _MODEL_RATES_CACHE[model_name]
    # Fallback: family median.
    from tokenscope.analytics import model_family

    return _FAMILY_RATES_CACHE.get(model_family(model_name))


def rates_for_family(family: str) -> FamilyRates | None:
    """Per-MTok rates for a model family (`opus` / `sonnet` / `haiku`),
    median across the versions LiteLLM publishes for that family."""
    if not _ensure_loaded():
        return None
    assert _FAMILY_RATES_CACHE is not None
    return _FAMILY_RATES_CACHE.get(family)


def reset_cache() -> None:
    """Clear in-process memoisation. Used by tests to force a re-fetch."""
    global _PRICING_DATA_CACHE, _FAMILY_RATES_CACHE, _MODEL_RATES_CACHE
    _PRICING_DATA_CACHE = None
    _FAMILY_RATES_CACHE = None
    _MODEL_RATES_CACHE = None
