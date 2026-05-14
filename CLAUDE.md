# tokenscope

**Repository:** https://github.com/Quintin-John/tokenscope.git

A local-first observability tool for Claude Code usage and cost tracking. Parses local Claude Code session logs, computes token usage and costs, exports metrics via OpenTelemetry, and provides Grafana dashboards for analysis. Conceptually similar to ccusage but with full OTEL pipeline and Grafana visualization.

---

## Operating constraints (non-negotiable)

These rules apply to every task in this project. Do not deviate without explicit confirmation.

1. **No guessing, no hand-waving.** Read the actual code, configuration, and documentation. Quote file paths and line numbers when making claims about existing state.
2. **Prove facts from source.** If you assert something works a certain way, cite the file and lines that demonstrate it. If you cannot, say so explicitly rather than inventing an answer.
3. **No invented APIs.** If you do not know the exact signature of a .NET API, OTEL method, NuGet package, or library call, look it up or ask. Do not generate plausible-looking code that may not compile.
4. **Wait for "go" between phases.** Do not move from analysis to implementation, or from one major phase to the next, without explicit confirmation. Stop at every phase boundary and summarize what was done.
5. **Cache discipline.** This CLAUDE.md is the stable context for the project. Do not modify it during a session unless explicitly asked. Load it once at session start, reference it as needed, let context grow by appending.
6. **Cross-platform from day one.** Every file path, log discovery routine, and OS-specific operation must work on both Windows and macOS. No Linux-only assumptions, no hardcoded path separators. Use `Path.Combine` and `Environment.GetFolderPath` rather than string concatenation.
7. **Verify before committing pricing or schema details.** Anthropic pricing changes. Claude Code log formats may change. Before implementing anything that depends on external schemas or rates, verify the current state by reading the source (logs, docs, pricing page) rather than relying on assumptions in this document.

---

## Initial action

Before doing anything else:

1. Clone the repository: `git clone https://github.com/Quintin-John/tokenscope.git`
2. `cd tokenscope`
3. List the current contents with `ls -la` (or `dir` on Windows)
4. Report back what is currently in the repository
5. Wait for "go" before creating any files

Do not assume the repository is empty. Do not assume it has any structure. Read what is actually there first.

---

## Project goals

tokenscope provides developers and engineering teams with visibility into Claude Code token usage and cost. The primary problem it solves: most users do not realize that LLM conversations re-bill the entire context on every turn, that prompt caching can dramatically reduce cost when used well, and that cache hit ratios are a meaningful efficiency metric. Without per-developer cost attribution, organizations either over-restrict access or accept opaque bills.

tokenscope addresses this by:

- Parsing local Claude Code session logs
- Computing per-session, per-day, and per-week cost using current Anthropic pricing
- Tracking cache efficiency (hit ratios, write/read ratios, estimated savings)
- Exporting metrics via OpenTelemetry to a local Grafana stack
- Providing a CLI for ad-hoc queries without needing the full stack running

The tool is local-first. No prompt content, file contents, or tool result bodies ever leave the host. Only token counts, model identifiers, timestamps, cache statistics, and session IDs are exported as metrics.

---

## Architectural decisions (fixed)

These are decided. Do not propose alternatives unless you find a concrete blocker, in which case stop and report.

| Decision | Choice |
|---|---|
| Language | C# on .NET 8 (LTS) |
| Style | Modern C#: file-scoped namespaces, primary constructors, records, nullable reference types enabled |
| Data scope | Claude Code local session logs only (no API integration, no Admin API, no remote sources) |
| Deployment | Full OpenTelemetry stack via docker-compose |
| Stack components | tokenscope collector (C# host binary) → OTEL Collector → Prometheus → Grafana |
| Metric protocol | OTLP over gRPC from collector to OTEL Collector. Prometheus scrapes OTEL Collector. Grafana queries Prometheus. |
| Pricing source | Versioned JSON config file. Rates verified against current Anthropic public pricing page at implementation time. |
| Subscription handling | Enterprise mode shows real cost. Pro and Max modes show inferred API-equivalent cost, clearly labeled. |
| Cache TTL pricing | Support both 5-minute (1.25x write) and 1-hour (2.0x write) TTL pricing. |
| Privacy | Token counts and metadata only. No content payloads ever exported. |
| License | MIT (change in Phase 1 if your shop requires different) |
| Testing | xUnit + FluentAssertions + NSubstitute |
| Target OSes | Windows 10+ and macOS 12+ (Linux not a requirement but should not be actively broken) |

---

## Repository structure

Generate exactly this structure in Phase 1. Confirm the file list before creating any files.

```
tokenscope/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── release.yml
├── src/
│   ├── TokenScope.Core/
│   ├── TokenScope.Otel/
│   └── TokenScope.Collector/
├── tests/
│   ├── TokenScope.Core.Tests/
│   ├── TokenScope.Otel.Tests/
│   └── TokenScope.Collector.Tests/
├── docker/
│   ├── docker-compose.yml
│   ├── otel-collector-config.yaml
│   ├── prometheus.yml
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/
│       │   │   └── prometheus.yaml
│       │   └── dashboards/
│       │       └── dashboards.yaml
│       └── dashboards/
│           └── tokenscope.json
├── config/
│   ├── pricing.json
│   └── tokenscope.example.yaml
├── scripts/
│   ├── install-windows.ps1
│   └── install-macos.sh
├── docs/
│   ├── architecture.md
│   ├── pricing-model.md
│   ├── metric-reference.md
│   └── troubleshooting.md
├── README.md
├── LICENSE
├── .editorconfig
├── .gitignore
├── Directory.Build.props
├── Directory.Packages.props
├── TokenScope.sln
└── CLAUDE.md
```

Default output is human-readable tables. `--json` flag produces machine-readable output. `--no-color` flag disables ANSI colors.

### Subscription mode

Config flag `subscription_mode` with values:

- `enterprise` — Real cost from token counts × public rates
- `pro` — Inferred API-equivalent cost, labeled "Inferred (subscription is flat-rate)"
- `max5x` — Same as pro, labeled "Inferred (Max 5x subscription is flat-rate at $100/month)"
- `max20x` — Same as pro, labeled "Inferred (Max 20x subscription is flat-rate at $200/month)"

Token counts and cache statistics display identically across modes. Only cost labels and a banner in Grafana differ.

---

## Testing requirements

xUnit + FluentAssertions + NSubstitute. Tests in `tests/` mirror the `src/` layout.

**Coverage targets:**
- `TokenScope.Core`: 90%+ line coverage
- `TokenScope.Otel`: 80%+
- `TokenScope.Collector`: 70%+ (file watching is integration-heavy)

**Mandatory test cases:**

1. Cost calculator with each component (input, output, cache read, cache write) verified independently against hand-computed values
2. Cost calculator edge cases: zero tokens, very large counts (test for overflow), missing model in pricing config
3. Cache cost for both 5m and 1h TTLs verified
4. Parser handles malformed log entries (corrupt JSON, missing fields, wrong types) by logging warnings and continuing
5. Parser handles partial reads (file open for writing) using sharing flags
6. Log discovery returns correct path on Windows and macOS — use `[Fact]` with platform conditionals or `[SkippableFact]`
7. OTEL metrics emit with correct labels (use in-memory meter listener for verification)
8. Subscription mode toggle changes labels but not values
9. Pricing config validation rejects invalid schemas (missing fields, negative rates, future effective_date in past records)
10. Date range aggregations use UTC internally, convert at display layer
11. Hot-reload of `pricing.json` picks up changes without restart
12. Aggregations are correct across timezone boundaries (test with sessions spanning midnight UTC)

Integration tests under `tests/*.Tests/Integration/` should spin up an in-memory `OpenTelemetry.Sdk` instance to verify end-to-end metric emission.

CI must run all tests on both `windows-latest` and `macos-latest` runners.

---

## Implementation phases

Work in phases. **Stop at every phase boundary and wait for "go" before proceeding.** At each boundary, summarize what was completed, what tests pass, and what is needed for the next phase.

### Phase 1: Foundation
- Inspect existing repo contents (per "Initial action" above)
- Create directory structure
- Generate `.gitignore`, `.editorconfig`, `Directory.Build.props`
- Create solution and empty projects with correct references
- Stub `Program.cs` files that build but do nothing
- CI workflow that builds and runs tests on Windows + macOS
- README skeleton

**Deliverable:** `dotnet build` and `dotnet test` succeed on both OSes with no real tests yet. Commit and push. Wait for "go."

### Phase 2: Core domain
- Domain models as immutable records
- Pricing config schema, loader, hot-reload
- Cost calculator with full unit test coverage (target 90%+)
- `docs/pricing-model.md` documenting the cost math with worked examples

**Deliverable:** Cost engine with passing tests. Commit and push. Wait for "go."

### Phase 3: Log parser
- Request sample log file path from developer
- Inspect actual schema, document in `docs/architecture.md`
- Implement parser with malformed-entry handling
- Unit tests using fixture log files in `tests/TokenScope.Core.Tests/Fixtures/`

**Deliverable:** Parser ingesting real logs with tests passing. Wait for "go."

### Phase 4: OTEL instrumentation
- Meter setup, all nine metrics defined
- OTLP exporter configured
- Tests verifying metric emission and labels
- `docs/metric-reference.md` listing every metric with type, unit, labels, description

**Deliverable:** Metrics flowing to a local OTLP endpoint. Wait for "go."

### Phase 5: Collector host
- `TokenScope.Collector` as a .NET Worker Service
- Integrate parser, cost engine, OTEL meters
- `FileSystemWatcher` with debouncing and retry
- Initial scan of pre-existing logs
- Graceful shutdown via `IHostApplicationLifetime`
- Configurable via `tokenscope.yaml`

**Deliverable:** Running collector binary watching logs and emitting metrics. Wait for "go."

### Phase 6: Docker stack
- `docker-compose.yml` with all four services
- OTEL Collector config (verify current image tag)
- Prometheus config
- Grafana provisioning files
- `docker-compose up` produces a working stack
- `docs/troubleshooting.md` covers common issues

**Deliverable:** Full stack running end-to-end. Wait for "go."

### Phase 7: Grafana dashboards
- One unified dashboard (`docker/grafana/dashboards/tokenscope.json`)
  organized as collapsible rows. Replaces the original four-dashboard
  spec; rationale in `docs/architecture.md`.
- Rows: Mode banner • At a glance • Cost trends • Sessions •
  Cache efficiency • Cost breakdown detail (collapsed) • Stack health
  (collapsed)
- Template variables: `$model`, `$project_name`, `$subscription_mode`
- Default time range: last 24 hours
- Tested with real metrics flowing
- Screenshots in README captured via the Grafana image renderer

**Deliverable:** Single tokenscope dashboard rendering real data. Wait for "go."

### Phase 8: --validate-pricing flag (no CLI)
- A CLI project was originally specified, then dropped after review
  concluded that status / cost / sessions / cache-efficiency would
  duplicate the dashboard. The only real non-dashboard use case is
  pre-deploy validation of `pricing.json` in CI scripts.
- Phase 8 collapses to a single `--validate-pricing <path>` flag on
  the existing `TokenScope.Collector` binary:
  - Validates without starting any hosted services.
  - Exits 0 on success, 4 on `PricingValidationException` (errors to stderr).
  - Used in CI via `docker compose run --rm tokenscope-collector --validate-pricing /data/config/pricing.json`.
- `TokenScope.Cli` and `TokenScope.Cli.Tests` projects were deleted
  (would have been dead weight otherwise).

**Deliverable:** validate-pricing flag with passing tests. Wait for "go."

### Phase 9: Polish and release
- Documentation complete
- Installation scripts (`scripts/install-windows.ps1`, `scripts/install-macos.sh`)
- Release workflow producing signed binaries
- README with screenshots, badges, install instructions
- CHANGELOG.md
- Tag v0.1.0

**Deliverable:** Ready for v0.1.0 release. Repository public-ready.

---

## Git workflow

- Branch per phase: `phase-1-foundation`, `phase-2-core-domain`, etc.
- Open a PR at the end of each phase for review
- Squash-merge to `main` after approval
- Tag releases with semantic versioning

Commit messages follow Conventional Commits:
- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `test:` tests only
- `refactor:` no functional change
- `chore:` tooling, deps

---

## What to do now

1. Confirm you have read and understood this entire document.
2. Confirm you understand the seven operating constraints.
3. Clone the repository per the "Initial action" section.
4. Report current repository contents.
5. List the exact files you will create in Phase 1.
6. Wait for "go."

Do not generate any code, modify any files, or start Phase 2 until explicitly told to.
