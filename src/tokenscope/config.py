"""Tokenscope operator-tunable settings, loaded from `tokenscope.config.toml`.

Reads the TOML once at module import. Falls back to baked-in defaults
when the file is missing or a key isn't set, so the dashboard still
works if someone deletes `tokenscope.config.toml`. The defaults are
the original hard-coded values from earlier slices — moving them here
is the "config-not-code" win without changing behaviour.

The module exposes plain constants (`DEFAULT_RANGE_DAYS` etc.) for
backwards compatibility with the import sites that used to read those
from individual modules.

Reload requires a Streamlit restart; we don't watch the TOML for
changes.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


# Search order for the config file. First hit wins.
_SEARCH_PATHS = (
    # Same directory as the source tree's root (next to pyproject.toml).
    Path(__file__).resolve().parent.parent.parent / "tokenscope.config.toml",
    # User-level config (for installations that aren't run from source).
    Path.home() / ".config" / "tokenscope" / "config.toml",
)


def _load() -> dict[str, Any]:
    for path in _SEARCH_PATHS:
        if path.is_file():
            try:
                return tomllib.loads(path.read_text())
            except (OSError, tomllib.TOMLDecodeError):
                continue
    return {}


_RAW: dict[str, Any] = _load()


def _get(section: str, key: str, default: Any) -> Any:
    return _RAW.get(section, {}).get(key, default)


# --- public constants -------------------------------------------------------

# [dashboard]
DEFAULT_RANGE_DAYS: int = int(_get("dashboard", "default_range_days", 30))
DATA_CACHE_TTL_SECONDS: int = int(_get("dashboard", "data_cache_ttl_seconds", 30))

# [live]
LIVE_REFRESH_SECONDS: int = int(_get("live", "refresh_seconds", 30))

# Wall-clock minutes per token-throughput bucket on the Live view.
# The throughput chart's percent-stacked area is meaningful only once
# at least `LIVE_THROUGHPUT_MIN_BUCKETS` buckets of data have
# accumulated; before that, the chart layer renders an empty-state
# panel rather than a degenerate single-column plot.
LIVE_THROUGHPUT_BUCKET_MINUTES: int = int(
    _get("live", "throughput_bucket_minutes", 5)
)
LIVE_THROUGHPUT_MIN_BUCKETS: int = int(
    _get("live", "throughput_min_buckets", 2)
)

# [overview]
# A day's cost qualifies as a "spike" worth annotating on the Overview
# cost chart when it exceeds this multiplier × the window's median
# daily cost. Lower → more sensitive (annotate routine highs); higher
# → only call out genuinely extreme days. 3.0 is the conventional
# "3× median = outlier" heuristic.
OVERVIEW_SPIKE_THRESHOLD: float = float(
    _get("overview", "spike_threshold_median_multiplier", 3.0)
)


# [pricing]
PRICING_LITELLM_URL: str = str(
    _get(
        "pricing",
        "litellm_url",
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json",
    )
)
PRICING_CACHE_DIR: Path = Path(
    str(_get("pricing", "cache_dir", "~/.cache/tokenscope"))
).expanduser()
PRICING_CACHE_TTL_SECONDS: int = (
    int(_get("pricing", "cache_ttl_days", 7)) * 24 * 3600
)
PRICING_FETCH_TIMEOUT_SECONDS: int = int(
    _get("pricing", "fetch_timeout_seconds", 10)
)
