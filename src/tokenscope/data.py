"""Streamlit-cached wrappers over the raw ccusage subprocess calls.

The dashboard imports `tokenscope.data` rather than `tokenscope.ccusage`
directly. Every call goes through `@st.cache_data(ttl=30)`, so the same
query inside a 30-second window doesn't re-shell to ccusage.

Caching strategy: we cache the **raw ccusage JSON dict** (which pickles
cleanly) and rebuild the pydantic model on every call. Earlier
implementations cached the model itself, which broke two ways:

  - `cache_data` rejected pydantic models for some shapes
    (`UnserializableReturnValueError` — Streamlit's serializer is
    stricter than plain `pickle.dumps`).
  - `cache_resource` returned the *same* object across hot-reloads, so
    after a code edit the cached model's class identity diverged from
    the freshly-imported one and downstream pydantic `isinstance` checks
    started failing.

Caching the JSON sidesteps both. The model build is microseconds for
ccusage-sized payloads, so the cost is negligible.

`Query` is a frozen dataclass — hashable, so Streamlit can key the cache
on it. All cache keys are immutable.

Implementation note: `_run_json` and `_coerce_empty` are accessed
through the `ccusage` module attribute (NOT via `from ccusage import
_run_json`). That way tests can monkeypatch `tokenscope.ccusage._run_json`
and the patch is honoured here.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from tokenscope import ccusage, config
from tokenscope.models import (
    BlocksReport,
    DailyByProjectReport,
    DailyReport,
    MonthlyByProjectReport,
    MonthlyReport,
    SessionReport,
    WeeklyByProjectReport,
    WeeklyReport,
)
from tokenscope.query import Query

_TTL = config.DATA_CACHE_TTL_SECONDS


# ---- raw-JSON cached transport ----


@st.cache_data(ttl=_TTL)
def _raw(
    subcommand: str,
    query: Query | None,
    *,
    coerce_key: str | None = None,
    project_view: bool = False,
    active: bool = False,
) -> Any:
    """Single cached transport function. Builds argv, shells out, applies
    the empty-result coercion when needed.

    The argument signature is the cache key, so calls that differ in any
    of (subcommand, query, coerce_key, project_view, active) get distinct
    cache slots. Earlier per-subcommand wrappers (`_daily_raw`,
    `_weekly_raw`, … 8 of them) all collapsed into this one function.

    Empty-range coercion: ccusage returns a bare ``[]`` instead of the
    expected ``{"<key>": [], "totals": {...}}`` for empty windows.
    `coerce_key=None` skips the coercion (used for `blocks` whose JSON
    shape stays consistent across empty/non-empty results).
    """
    args: list[str] = [subcommand]
    if active:
        args.append("--active")
    if project_view:
        args.append("--instances")
    args.extend(Query.argv(query))
    raw = ccusage._run_json(args)
    if coerce_key is None:
        return raw
    container_type = dict if project_view else list
    return ccusage._coerce_empty(raw, coerce_key, container_type=container_type)


# ---- model-validating public layer ----

def daily(query: Query | None = None) -> DailyReport:
    return DailyReport.model_validate(_raw("daily", query, coerce_key="daily"))


def weekly(query: Query | None = None) -> WeeklyReport:
    return WeeklyReport.model_validate(_raw("weekly", query, coerce_key="weekly"))


def monthly(query: Query | None = None) -> MonthlyReport:
    return MonthlyReport.model_validate(_raw("monthly", query, coerce_key="monthly"))


def session(query: Query | None = None) -> SessionReport:
    return SessionReport.model_validate(_raw("session", query, coerce_key="sessions"))


def blocks(active: bool = False, query: Query | None = None) -> BlocksReport:
    return BlocksReport.model_validate(_raw("blocks", query, active=active))


def daily_by_project(query: Query | None = None) -> DailyByProjectReport:
    return DailyByProjectReport.model_validate(
        _raw("daily", query, coerce_key="projects", project_view=True)
    )


def weekly_by_project(query: Query | None = None) -> WeeklyByProjectReport:
    return WeeklyByProjectReport.model_validate(
        _raw("weekly", query, coerce_key="projects", project_view=True)
    )


def monthly_by_project(query: Query | None = None) -> MonthlyByProjectReport:
    return MonthlyByProjectReport.model_validate(
        _raw("monthly", query, coerce_key="projects", project_view=True)
    )
