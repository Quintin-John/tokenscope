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
from typing import TypedDict

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_CACHE_DIR = Path.home() / ".cache" / "tokenscope"
_CACHE_FILE = _CACHE_DIR / "litellm_pricing.json"
_CACHE_TTL_SECONDS = 7 * 24 * 3600
_FETCH_TIMEOUT_SECONDS = 10


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
                return json.loads(_CACHE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    try:
        with urllib.request.urlopen(
            LITELLM_PRICING_URL, timeout=_FETCH_TIMEOUT_SECONDS
        ) as resp:
            data = json.loads(resp.read())
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(json.dumps(data))
        except OSError:
            pass  # cache write is best-effort; failure isn't fatal.
        return data
    except (urllib.error.URLError, json.JSONDecodeError, OSError, TimeoutError):
        pass

    # Stale cache as last resort.
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _per_token_to_per_mtok(per_token: float | None) -> float:
    return float(per_token) * 1_000_000 if per_token else 0.0


def _build_family_rates(pricing_data: dict) -> dict[str, FamilyRates]:
    """Group Claude models in the LiteLLM schema by family, pick the
    median rate across versions within each family.

    Median is robust to one anomalous version (e.g. a deprecated
    discounted entry) pulling the family's rate sideways.
    """
    # Local import to avoid analytics ↔ pricing cycle at import time.
    from tokenscope.analytics import model_family

    by_family: dict[str, list[dict]] = {}
    for model_id, info in pricing_data.items():
        if not isinstance(info, dict) or not model_id.startswith("claude-"):
            continue
        # Skip entries that are clearly bedrock/vertex aliases — they
        # often have different pricing and would skew the median.
        if "/" in model_id or model_id.endswith(":beta"):
            continue
        family = model_family(model_id)
        by_family.setdefault(family, []).append(info)

    rates: dict[str, FamilyRates] = {}
    for family, entries in by_family.items():
        def med(key: str) -> float:
            vals = [
                _per_token_to_per_mtok(e[key])
                for e in entries
                if e.get(key) is not None
            ]
            return float(median(vals)) if vals else 0.0

        rates[family] = FamilyRates(
            input=med("input_cost_per_token"),
            output=med("output_cost_per_token"),
            cache_create=med("cache_creation_input_token_cost"),
            cache_read=med("cache_read_input_token_cost"),
        )
    return rates


def _build_model_rates(pricing_data: dict) -> dict[str, FamilyRates]:
    """Per-model rate dictionary: exact LiteLLM key → rates.

    Lets the consumer hit the precise rate for `claude-opus-4-7` rather
    than averaging across every opus-* alias (the rate sometimes shifts
    significantly between version numbers).
    """
    out: dict[str, FamilyRates] = {}
    for model_id, info in pricing_data.items():
        if not isinstance(info, dict) or not model_id.startswith("claude-"):
            continue
        if "/" in model_id or model_id.endswith(":beta"):
            continue
        out[model_id] = FamilyRates(
            input=_per_token_to_per_mtok(info.get("input_cost_per_token")),
            output=_per_token_to_per_mtok(info.get("output_cost_per_token")),
            cache_create=_per_token_to_per_mtok(
                info.get("cache_creation_input_token_cost")
            ),
            cache_read=_per_token_to_per_mtok(
                info.get("cache_read_input_token_cost")
            ),
        )
    return out


def _ensure_loaded() -> bool:
    global _PRICING_DATA_CACHE, _FAMILY_RATES_CACHE, _MODEL_RATES_CACHE
    if _PRICING_DATA_CACHE is not None:
        return True
    data = _fetch_pricing_json()
    if data is None:
        return False
    _PRICING_DATA_CACHE = data
    _FAMILY_RATES_CACHE = _build_family_rates(data)
    _MODEL_RATES_CACHE = _build_model_rates(data)
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
