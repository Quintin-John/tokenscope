"""Streamlit-cached wrappers over the raw ccusage subprocess calls.

The dashboard imports `tokenscope.data` rather than `tokenscope.ccusage`
directly. Each call goes through `@st.cache_data(ttl=30)`, so the same
query inside a 30-second window does not re-shell to ccusage.

The wrapped functions accept a `Query` (frozen dataclass — hashable, so
Streamlit can key the cache on it). All cache keys are immutable.
"""

from __future__ import annotations

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


@st.cache_data(ttl=_TTL)
def daily(query: Query | None = None) -> DailyReport:
    return ccusage.daily(query)


@st.cache_data(ttl=_TTL)
def weekly(query: Query | None = None) -> WeeklyReport:
    return ccusage.weekly(query)


@st.cache_data(ttl=_TTL)
def monthly(query: Query | None = None) -> MonthlyReport:
    return ccusage.monthly(query)


@st.cache_data(ttl=_TTL)
def session(query: Query | None = None) -> SessionReport:
    return ccusage.session(query)


@st.cache_data(ttl=_TTL)
def blocks(active: bool = False, query: Query | None = None) -> BlocksReport:
    return ccusage.blocks(active=active, query=query)


@st.cache_data(ttl=_TTL)
def daily_by_project(query: Query | None = None) -> DailyByProjectReport:
    return ccusage.daily_by_project(query)


@st.cache_data(ttl=_TTL)
def weekly_by_project(query: Query | None = None) -> WeeklyByProjectReport:
    return ccusage.weekly_by_project(query)


@st.cache_data(ttl=_TTL)
def monthly_by_project(query: Query | None = None) -> MonthlyByProjectReport:
    return ccusage.monthly_by_project(query)
