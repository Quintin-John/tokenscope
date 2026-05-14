# tokenscope

A local-first observability tool for Claude Code usage and cost tracking.

Parses local Claude Code session logs, computes token usage and costs, exports
metrics via OpenTelemetry, and provides Grafana dashboards for analysis. No
prompt content or tool result bodies ever leave the host — only token counts,
model identifiers, timestamps, cache statistics, and session IDs are exported
as metrics.

## Status

Phase 6 (full Docker stack). All four services run via `docker compose`:
collector + OTEL Collector + Prometheus + Grafana. See
[CLAUDE.md](./CLAUDE.md) for the full project plan and build phases.

## Quick start

Prerequisites: Docker Desktop (macOS / Windows) or Docker Engine (Linux).
Claude Code already installed and used at least once, so
`~/.claude/projects/` exists with `.jsonl` session files.

```sh
cd docker
docker compose up
```

That's it — one command starts the whole pipeline:

| Service | URL | Purpose |
|---|---|---|
| Grafana | <http://localhost:3000> (admin / tokenscope) | Dashboards |
| _Everything else_ | internal-only | OTLP ingest, Prometheus scrape — not exposed |

The collector reads `${HOME}/.claude/projects/` as a read-only bind mount
inside its container, parses logs, computes cost via `config/pricing.json`,
and publishes OTLP to the OTEL Collector over the internal `tokenscope`
network.

See [`docs/architecture.md`](./docs/architecture.md) for the full
container layout and configuration precedence rules; see
[`docs/troubleshooting.md`](./docs/troubleshooting.md) when things
go wrong.

## Development build (without Docker)

```sh
dotnet restore
dotnet build
dotnet test
```

Requires the .NET 8 SDK (pinned via [`global.json`](./global.json)).
Useful for working on the code; for end-to-end testing of the OTEL
pipeline, use the docker-compose stack above.

## Configuration

| Where | What |
|---|---|
| [`config/tokenscope.example.yaml`](./config/tokenscope.example.yaml) | Collector behaviour: scan settings, OTLP endpoint, subscription mode, logging |
| [`config/pricing.json`](./config/pricing.json) | Anthropic per-model rates, versioned by `effective_date`. Hot-reloadable. |
| [`.env.example`](./.env.example) | docker-compose env overrides (Grafana password, polling toggle, etc.) |

Env vars with prefix `TOKENSCOPE_` override YAML values at runtime.
See [`docs/architecture.md` → "Configuration precedence"](./docs/architecture.md#configuration-precedence).

## License

[MIT](./LICENSE)
