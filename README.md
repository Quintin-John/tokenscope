# tokenscope

A local-first observability tool for Claude Code usage and cost tracking.

Parses local Claude Code session logs, computes token usage and costs, exports metrics via
OpenTelemetry, and provides Grafana dashboards for analysis. No prompt content or tool
result bodies ever leave the host — only token counts, model identifiers, timestamps,
cache statistics, and session IDs are exported as metrics.

## Status

Phase 1 (foundation). Solution builds and a smoke test passes. No real functionality yet.

See [CLAUDE.md](./CLAUDE.md) for the full project plan and build phases.

## Build

```sh
dotnet restore
dotnet build
dotnet test
```

Requires the .NET 8 SDK (pinned via [`global.json`](./global.json)).

## License

[MIT](./LICENSE)
