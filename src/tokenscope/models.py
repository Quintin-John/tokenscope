"""Pydantic models for ccusage's --json output shapes.

Field names use snake_case in Python and map to ccusage's camelCase JSON via
Field aliases. `extra="forbid"` is deliberate: if ccusage adds an unexpected
field we want a loud test failure, not silent drift.

Verified against ccusage 18.0.11. The WeeklyEntry shape is inferred from the
daily / monthly pattern and is confirmed against a captured fixture at test
time (see tests/test_ccusage_parse.py).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _BaseShape(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ModelBreakdown(_BaseShape):
    model_name: str = Field(alias="modelName")
    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_creation_tokens: int = Field(alias="cacheCreationTokens")
    cache_read_tokens: int = Field(alias="cacheReadTokens")
    cost: float


class _PeriodEntryBase(_BaseShape):
    """Shared fields across daily / weekly / monthly entries."""

    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_creation_tokens: int = Field(alias="cacheCreationTokens")
    cache_read_tokens: int = Field(alias="cacheReadTokens")
    total_tokens: int = Field(alias="totalTokens")
    total_cost: float = Field(alias="totalCost")
    models_used: list[str] = Field(alias="modelsUsed")
    model_breakdowns: list[ModelBreakdown] = Field(alias="modelBreakdowns")


class Totals(_BaseShape):
    """Top-level summary across all entries in a daily/weekly/monthly/session report."""

    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_creation_tokens: int = Field(alias="cacheCreationTokens")
    cache_read_tokens: int = Field(alias="cacheReadTokens")
    total_tokens: int = Field(alias="totalTokens")
    total_cost: float = Field(alias="totalCost")


class DailyEntry(_PeriodEntryBase):
    date: str
    # ccusage adds a `project` field per entry only when invoked with
    # `--project=<id>`. Optional everywhere else; we keep extra="forbid"
    # elsewhere to catch unannounced ccusage schema drift.
    project: str | None = None


class DailyReport(_BaseShape):
    daily: list[DailyEntry]
    totals: Totals


class WeeklyEntry(_PeriodEntryBase):
    week: str


class WeeklyReport(_BaseShape):
    weekly: list[WeeklyEntry]
    totals: Totals


class MonthlyEntry(_PeriodEntryBase):
    month: str


class MonthlyReport(_BaseShape):
    monthly: list[MonthlyEntry]
    totals: Totals


class SessionEntry(_PeriodEntryBase):
    session_id: str = Field(alias="sessionId")
    last_activity: str = Field(alias="lastActivity")
    project_path: str = Field(alias="projectPath")


class SessionReport(_BaseShape):
    sessions: list[SessionEntry]
    totals: Totals


class BlockTokenCounts(_BaseShape):
    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_creation_input_tokens: int = Field(alias="cacheCreationInputTokens")
    cache_read_input_tokens: int = Field(alias="cacheReadInputTokens")


class BurnRate(_BaseShape):
    tokens_per_minute: float = Field(alias="tokensPerMinute")
    tokens_per_minute_for_indicator: float = Field(alias="tokensPerMinuteForIndicator")
    cost_per_hour: float = Field(alias="costPerHour")


class Projection(_BaseShape):
    total_tokens: int = Field(alias="totalTokens")
    total_cost: float = Field(alias="totalCost")
    remaining_minutes: int = Field(alias="remainingMinutes")


class BlockEntry(_BaseShape):
    id: str
    start_time: str = Field(alias="startTime")
    end_time: str = Field(alias="endTime")
    actual_end_time: str | None = Field(alias="actualEndTime")
    is_active: bool = Field(alias="isActive")
    is_gap: bool = Field(alias="isGap")
    entries: int
    token_counts: BlockTokenCounts = Field(alias="tokenCounts")
    total_tokens: int = Field(alias="totalTokens")
    cost_usd: float = Field(alias="costUSD")
    models: list[str]
    burn_rate: BurnRate | None = Field(alias="burnRate")
    projection: Projection | None


class BlocksReport(_BaseShape):
    blocks: list[BlockEntry]


class DailyByProjectReport(_BaseShape):
    """Shape returned by `ccusage daily --instances --json`."""

    projects: dict[str, list[DailyEntry]]
    totals: Totals


class WeeklyByProjectReport(_BaseShape):
    projects: dict[str, list[WeeklyEntry]]
    totals: Totals


class MonthlyByProjectReport(_BaseShape):
    projects: dict[str, list[MonthlyEntry]]
    totals: Totals
