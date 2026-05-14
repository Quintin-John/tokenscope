# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-14

Initial release. Local-first observability for Claude Code usage and
cost, delivered as a four-service Docker stack.

### What's in it

**Collector** (`TokenScope.Collector`)

- Parses `~/.claude/projects/**/*.jsonl` Claude Code session logs.
- Schema verified live against 13 real session files (~42,000 events)
  across 10 project directories — see `docs/architecture.md`.
- Dedup by `(sessionId, requestId)` (same `requestId` can appear in
  multiple log entries with identical usage; naïve aggregation would
  double-count).
- Resume from `~/.tokenscope/state/seen.json` across restarts — no
  re-processing already-counted events.
- Hot-reload of `config/pricing.json` via `FileSystemWatcher`.
- Polling fallback for macOS bind-mounted directories (Docker Desktop's
  gRPC FUSE doesn't propagate inotify events reliably). Default ON in
  `docker-compose.yml`.
- `--validate-pricing <path>` flag for CI gates. Exits 0 / 4 / 2.

**Cost engine** (`TokenScope.Core`)

- Decimal arithmetic for USD costs.
- Versioned pricing config with `effective_date` per entry; lookup by
  request timestamp (not current time) so historical correctness holds
  through pricing changes.
- USD-only at v1; non-USD currency in `pricing.json` rejected at load.
- Typed `CostResult` discriminated union: `Success` / `ModelNotFound` /
  `NoRateEffective`. No defaulting, no swallowed errors.

**OpenTelemetry** (`TokenScope.Otel`)

- Eight metrics on meter `tokenscope`: four token counters, USD cost,
  request count, cache hit ratio observable gauge, active-sessions
  observable gauge.
- Labels include `model`, `session_id`, `project` (lossless encoded
  cwd), `project_name` (friendly last-segment display).
- Cost unit is the OTEL annotation form `{usd}` so the Prometheus
  exporter doesn't append a redundant `_USD` suffix.

**Receiver stack** (`docker/`)

- `otel/opentelemetry-collector-contrib:0.151.0`
- `prom/prometheus:v3.5.3` (LTS line, 30-day retention)
- `grafana/grafana:12.4.3` (last 12.x; avoids 13.x unified-storage
  migration on first run)
- Built from `mcr.microsoft.com/dotnet/sdk:8.0.421-bookworm-slim` →
  `mcr.microsoft.com/dotnet/runtime:8.0.27-bookworm-slim`

**Dashboard** (`docker/grafana/dashboards/tokenscope.json`)

- Single unified dashboard with seven collapsible rows (Mode banner,
  At a glance, Cost trends, Sessions, Cache efficiency, Cost breakdown
  detail, Stack health).
- Headline stat: **Saved by caching** — exact PromQL derivation,
  `sum(tokenscope_cost_usd_total{component="cache_read"}) * 9` (cache
  reads cost 0.1× input → savings ratio is 9:1).
- Counter-reset-safe cumulative cost timeseries via
  `increase()` + Grafana's Cumulative Sum field transform.
- Template variables for `$model`, `$project_name`, `$subscription_mode`.
- Designed for dark theme; light-theme variant deferred.

### Verification

- 146 tests passing locally on macOS (Apple Silicon, .NET 8.0.408 SDK).
- Coverage: `TokenScope.Core` 94.30%, `TokenScope.Otel` 100%,
  `TokenScope.Collector` ≥70%.
- End-to-end smoke verified on a real 3,849-request corpus: $9.11 over
  30 days with $72.76 saved by caching.

### Deliberate non-features

These were considered and intentionally not shipped at v0.1.0:

- **No CLI binary.** A CLI with `status` / `cost` / `sessions` /
  `cache-efficiency` / `query` subcommands was specified, then dropped
  after review concluded those would duplicate the dashboard. The only
  surviving non-dashboard use case (pre-deploy `pricing.json`
  validation) is addressed by the `--validate-pricing` flag on the
  collector itself.
- **No GitHub Actions / no automated CI.** `.github/workflows/` is
  gitignored. Tests run manually (`dotnet test`) before commit. This is
  a cost / scope decision, not an oversight.
- **No release workflow, no signed binaries.** Release artifact is the
  git tag `v0.1.0`; users clone and run `docker compose up`. No
  per-platform installer scripts.
- **No screenshot regeneration tooling.** The committed
  `docs/images/dashboard-full.png` is a one-time README artifact;
  re-render manually if the dashboard layout changes meaningfully.
- **Subscription-mode multiplier handling deferred.** Pricing modifiers
  in the log (`service_tier`, `inference_geo`, `speed`) are parsed and
  surfaced as `tokenscope.subscription_mode` resource attribute, but
  the cost engine itself is mode-agnostic at v0.1.0 — it always
  computes API-equivalent cost. Banner labelling happens at the
  dashboard layer.
- **Windows native verification.** Code uses cross-platform APIs
  (`Path.Combine`, `Environment.GetFolderPath`) per the operating
  constraints, but actual `dotnet test` on Windows is deferred until a
  maintainer has access.
- **`StrictKeyValidator` is lenient at the IConfiguration root** — the
  Host's `DOTNET_`-prefix env-var provider injects ambient keys we
  can't strip without breaking host internals. Typos at the
  `tokenscope.yaml` top level go uncaught; typos inside known sections
  still produce full dotted-path errors. TODO marker in the code.
- **In-memory dedup set is unbounded.** Long-running collectors with
  thousands of sessions could grow the set. LRU bound by session
  inactivity is a future polish item.

### Known limitations to revisit post-v0.1.0

- Light-theme dashboard variant.
- Per-project-directory aggregation panels in the dashboard.
- Drill-down links between dashboard panels.
- Mobile-friendly layout audit.
- Alpine runtime image for smaller container size (~80 MB vs ~190 MB);
  deferred for v1 because `musl` libc introduces an .NET edge-case
  risk class with no strategic value yet.

### Operating constraints (non-CI)

- .NET 8 LTS only. No move to .NET 9 or 10 without an explicit decision.
- Cross-platform from day one (macOS + Windows code paths;
  `Path.Combine`, no hardcoded separators).
- USD-only at v1 in pricing.
- Privacy: no prompt content, tool result bodies, or file contents
  ever leave the host. Only token counts and metadata are exported.

[0.1.0]: https://github.com/Quintin-John/tokenscope/releases/tag/v0.1.0
