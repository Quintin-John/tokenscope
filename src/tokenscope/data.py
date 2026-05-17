"""Streamlit-cached wrappers over the raw ccusage subprocess calls.

The dashboard imports `tokenscope.data` rather than `tokenscope.ccusage`
directly. Each call goes through `@st.cache_data(ttl=30)`, so the same
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

from tokenscope import ccusage
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

_TTL = 30


def _q_args(query: Query | None) -> list[str]:
    return query.to_args() if query is not None else []


# ---- raw-JSON cached layer ----

@st.cache_data(ttl=_TTL)
def _daily_raw(query: Query | None) -> Any:
    raw = ccusage._run_json(["daily", *_q_args(query)])
    return ccusage._coerce_empty(raw, "daily")


@st.cache_data(ttl=_TTL)
def _weekly_raw(query: Query | None) -> Any:
    raw = ccusage._run_json(["weekly", *_q_args(query)])
    return ccusage._coerce_empty(raw, "weekly")


@st.cache_data(ttl=_TTL)
def _monthly_raw(query: Query | None) -> Any:
    raw = ccusage._run_json(["monthly", *_q_args(query)])
    return ccusage._coerce_empty(raw, "monthly")


@st.cache_data(ttl=_TTL)
def _session_raw(query: Query | None) -> Any:
    raw = ccusage._run_json(["session", *_q_args(query)])
    return ccusage._coerce_empty(raw, "sessions")


@st.cache_data(ttl=_TTL)
def _blocks_raw(active: bool, query: Query | None) -> Any:
    args: list[str] = []
    if active:
        args.append("--active")
    args += _q_args(query)
    return ccusage._run_json(["blocks", *args])


@st.cache_data(ttl=_TTL)
def _daily_by_project_raw(query: Query | None) -> Any:
    raw = ccusage._run_json(["daily", "--instances", *_q_args(query)])
    return ccusage._coerce_empty(raw, "projects", container_type=dict)


@st.cache_data(ttl=_TTL)
def _weekly_by_project_raw(query: Query | None) -> Any:
    raw = ccusage._run_json(["weekly", "--instances", *_q_args(query)])
    return ccusage._coerce_empty(raw, "projects", container_type=dict)


@st.cache_data(ttl=_TTL)
def _monthly_by_project_raw(query: Query | None) -> Any:
    raw = ccusage._run_json(["monthly", "--instances", *_q_args(query)])
    return ccusage._coerce_empty(raw, "projects", container_type=dict)


# ---- model-validating public layer ----

def daily(query: Query | None = None) -> DailyReport:
    return DailyReport.model_validate(_daily_raw(query))


def weekly(query: Query | None = None) -> WeeklyReport:
    return WeeklyReport.model_validate(_weekly_raw(query))


def monthly(query: Query | None = None) -> MonthlyReport:
    return MonthlyReport.model_validate(_monthly_raw(query))


def session(query: Query | None = None) -> SessionReport:
    return SessionReport.model_validate(_session_raw(query))


def blocks(active: bool = False, query: Query | None = None) -> BlocksReport:
    return BlocksReport.model_validate(_blocks_raw(active, query))


def daily_by_project(query: Query | None = None) -> DailyByProjectReport:
    return DailyByProjectReport.model_validate(_daily_by_project_raw(query))


def weekly_by_project(query: Query | None = None) -> WeeklyByProjectReport:
    return WeeklyByProjectReport.model_validate(_weekly_by_project_raw(query))


def monthly_by_project(query: Query | None = None) -> MonthlyByProjectReport:
    return MonthlyByProjectReport.model_validate(_monthly_by_project_raw(query))
