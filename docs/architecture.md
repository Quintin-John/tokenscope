# Architecture: Claude Code session log schema

This document describes the on-disk format of Claude Code session logs as
observed on macOS, .NET 8, Claude Code versions `2.1.114` – `2.1.126`,
sampled across 13 real `.jsonl` files (10 KB – 112 MB) drawn from 10
different project directories on 2026-05-13. tokenscope's parser is
written against this verified schema; if Claude Code changes the
format, this document and the parser must be updated together.

## File layout

### macOS
```
~/.claude/projects/<encoded-project-path>/<session-uuid>.jsonl
```

### Windows
```
%USERPROFILE%\.claude\projects\<encoded-project-path>\<session-uuid>.jsonl
```

(The Windows path was not exercised in this audit — the user's
verification environment is macOS-only. The parser uses
`Environment.GetFolderPath(SpecialFolder.UserProfile)` + `Path.Combine`
so it should work on both. Windows-specific verification is deferred.)

### Encoded project path

Claude Code transforms the absolute project directory into the
`<encoded-project-path>` segment by replacing **both** path separators
and spaces with hyphens:

| Original `cwd`                            | Encoded directory name                    |
|-------------------------------------------|-------------------------------------------|
| `/Users/q/Documents/foo`                  | `-Users-q-Documents-foo`                  |
| `/Volumes/SSK Drive /ManageLiterature`    | `-Volumes-SSK-Drive--ManageLiterature`    |

This encoding is **lossy** (a `-` in the directory name could originally
have been `/`, ` `, or `-`). **Do not derive the project path from the
directory name.** Instead, read the `cwd` field from any envelope-bearing
event inside the file — that is the authoritative project path.

## JSONL format

- One JSON object per line. UTF-8. LF-terminated. No blank lines, no
  trailing comma, no comments.
- File ends with `\n`.
- Every line in the 1339 entries inspected across the small/medium/large
  samples parsed cleanly. **The parser must still be defensive** — crashes,
  partial writes during a session in progress, or future schema drift
  could produce malformed lines. Malformed lines are skipped with a
  warning; the parser continues from the next line.

## Session boundary

One file = one session. The filename's UUID matches the `sessionId`
field of every event inside the file. The parser never needs to infer
session boundaries from event content.

## Event types

`type` is the discriminator field. Across the audit, ten distinct values
were observed, frequencies (across 13 files, 41,920 events total):

| `type`                  | Count  | Cost-relevant? |
|-------------------------|--------|----------------|
| `assistant`             | 18,175 | **YES** — carries `message.usage` and `message.model` |
| `user`                  | 13,485 | No (no token usage) |
| `queue-operation`       | 3,298  | No |
| `file-history-snapshot` | 2,732  | **Privacy-sensitive — never read `.snapshot`** |
| `last-prompt`           | 1,289  | No |
| `pr-link`               | 826    | No |
| `attachment`            | 534    | No — privacy-sensitive payloads |
| `ai-title`              | 262    | No |
| `system`                | 111    | No (envelope-only metadata) |
| `permission-mode`       | 108    | No |

**Unknown `type` values** must be tolerated. The parser ignores any
event whose `type` it does not recognize.

## Common envelope

`assistant`, `user`, `attachment`, and `system` events share this
envelope. Other event types use a slimmer shape.

| Field        | Type                                | Notes |
|--------------|-------------------------------------|-------|
| `uuid`       | string (event UUID)                 | Unique within a session |
| `parentUuid` | string \| null                      | Threading link |
| `type`       | string                              | Discriminator (see above) |
| `timestamp`  | string `2026-05-12T21:16:33.881Z`   | ISO-8601, **always UTC `Z`**, millisecond precision. 1,003 / 1,003 timestamps in the audit matched this exact regex. |
| `sessionId`  | string (UUID)                       | Matches filename |
| `userType`   | string                              | Only `"external"` observed |
| `entrypoint` | string \| null                      | `"claude-vscode"`, `"cli"`, or null |
| `cwd`        | string                              | Absolute path of project directory (authoritative) |
| `version`    | string                              | Claude Code version, e.g. `"2.1.126"` |
| `gitBranch`  | string                              | Branch name or `"HEAD"` |
| `isSidechain`| bool                                | `true` for subagent fan-out (none observed in audit, but the field is part of the schema) |

## `assistant` event — the cost-bearing event

Adds `message` and `requestId` to the envelope.

```jsonc
{
  // ...common envelope...
  "requestId": "req_011CaBf8smdT157KQrak959X",
  "message": {
    "id":           "msg_01BZznnnCVW2L9UJWN8XMe6p",
    "type":         "message",
    "role":         "assistant",
    "model":        "claude-opus-4-7",
    "content":      [ /* array of content blocks — see below */ ],
    "stop_reason":  "end_turn",      // observed: "end_turn", "tool_use"
    "stop_details": null,            // null in all observed entries
    "stop_sequence": null,
    "usage":        { /* see below */ },
    "diagnostics":  { /* optional — see below */ }
  }
}
```

### `requestId` and dedupe

`requestId` is the canonical Anthropic API request identifier
(format: `req_*`). The audit found that the same `requestId` can appear
in **multiple consecutive assistant events** in the same session — up to
14 times for one outlier. **All events sharing a `requestId` carry
byte-identical `usage` objects.**

Implication: **naïve aggregation double-counts.** Cost calculation must
deduplicate by `(sessionId, requestId)`. The parser exposes a stable
key for downstream aggregators.

`requestId` was observed to be session-scoped — no `requestId` was
shared across sessions in the audit. The parser treats the
`(sessionId, requestId)` tuple as the unique key regardless.

### `message.usage` — the cost-critical block

```jsonc
{
  "input_tokens":                6,
  "output_tokens":             168,
  "cache_creation_input_tokens": 8102,   // total cache writes (5m + 1h)
  "cache_read_input_tokens":  14743,
  "cache_creation": {
    "ephemeral_5m_input_tokens": 0,
    "ephemeral_1h_input_tokens": 8102
  },
  "service_tier":   "standard",          // observed: "standard"
  "inference_geo":  "",                  // observed: "" (= global)
  "speed":          "standard",          // observed: "standard"
  "server_tool_use": {
    "web_search_requests": 0,
    "web_fetch_requests":  0
  },
  "iterations": [
    {
      "type": "message",
      "input_tokens":  6,
      "output_tokens": 168,
      "cache_read_input_tokens":     14743,
      "cache_creation_input_tokens": 8102,
      "cache_creation": {
        "ephemeral_5m_input_tokens": 0,
        "ephemeral_1h_input_tokens": 8102
      }
    }
  ]
}
```

**Field presence in the audit:** every one of the 465 `assistant` events
inspected had **all five** cost-component fields populated
(`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation.ephemeral_5m_input_tokens`,
`cache_creation.ephemeral_1h_input_tokens`). The parser still treats
each as nullable and defaults missing values to 0 with a warning.

**Mapping to the Phase 2 cost engine** (`TokenScope.Core.Domain.TokenUsage`):

| `TokenUsage` field   | Source field                                              |
|----------------------|-----------------------------------------------------------|
| `Input`              | `usage.input_tokens`                                      |
| `Output`             | `usage.output_tokens`                                     |
| `CacheRead`          | `usage.cache_read_input_tokens`                           |
| `CacheWrite5m`       | `usage.cache_creation.ephemeral_5m_input_tokens`          |
| `CacheWrite1h`       | `usage.cache_creation.ephemeral_1h_input_tokens`          |

`usage.cache_creation_input_tokens` is the **sum** of the 5m and 1h
components — the parser ignores it and reads the two TTL fields directly.
This avoids a cross-check class of bugs (top-level total drifting from
the breakdown).

`iterations` is also redundant with the top-level usage. In every
observed multi-iteration case (always 1 iteration in the audit), the
iteration tokens summed to the top-level usage. The parser ignores
`iterations` for cost; it may be surfaced later as a diagnostic.

### Pricing modifiers in the log

The log carries three pricing modifiers that the Phase 2 cost engine
does **not** model yet:

| Field           | Observed values | Multiplier if non-default | Tracked? |
|-----------------|-----------------|---------------------------|----------|
| `service_tier`  | `"standard"`    | `"batch"` → 0.5× discount | TODO     |
| `inference_geo` | `""` (global)   | `"us"` → 1.1× premium     | TODO     |
| `speed`         | `"standard"`    | `"fast"` → 6× premium     | TODO     |

The parser captures these fields so a future cost-engine extension can
apply them. They appear on the parsed `LogEntry.Usage` record but the
Phase 2 `CostCalculator` ignores them.

### `message.content` — privacy-sensitive

Array of blocks. Three block types observed for `assistant`:

| Block type  | Fields                                  | Privacy |
|-------------|-----------------------------------------|---------|
| `text`      | `{type, text}`                          | **Do not read `text`** |
| `thinking`  | `{type, thinking, signature}`           | **Do not read `thinking`** |
| `tool_use`  | `{type, id, name, input, caller}`       | **Do not read `input`** — may contain prompt contents |

The parser exposes only the block **count** and the tool names
(`tool_use.name`) for diagnostic purposes. Bodies are skipped entirely.

### `message.diagnostics`

Optional. Observed shape: `{ "cache_miss_reason": "..." }`. Diagnostic
metadata, not cost-affecting. The parser passes it through as opaque
JSON.

## `user` event

Four variants distinguished by which optional fields are present
(audit counts in the 3-file sample):

| Variant                              | Distinguishing fields                                  | Count |
|--------------------------------------|--------------------------------------------------------|-------|
| Plain user prompt                    | `promptId`                                             | many  |
| Tool result wrapper                  | `toolUseResult`, `sourceToolAssistantUUID`             | 253   |
| Permission-mode transition           | `permissionMode`                                       | 81    |
| Meta event                           | `isMeta: true`                                         | 1     |

`message.content` is mostly an array (335) but occasionally a plain
string (4) — the parser handles both.

Content block types within `user.message.content` (array form):
`text` (117) and `tool_result` (253). **Both are privacy-sensitive**;
the parser does not read block bodies.

User events carry no `usage` or `model` — they do not contribute to cost.

## `file-history-snapshot` event — privacy

```json
{ "type": "file-history-snapshot", "messageId": "...", "snapshot": { /* file contents */ }, "isSnapshotUpdate": false }
```

Average size in the audit: **4099 bytes per entry**. `snapshot` contains
file contents at a point in time. **The parser must never deserialize
`snapshot`** — it skips the entire entry after type discrimination.

## Other event types (briefly)

- **`attachment`**: file-list / skill-list / prompt-source payloads. Privacy-sensitive `content` field; ignored.
- **`queue-operation`**: `enqueue` / `dequeue` / `remove`. Operational only.
- **`last-prompt`**: bookmark of the last user prompt. Privacy-sensitive `lastPrompt`; ignored.
- **`permission-mode`**: state transitions like `plan` / `acceptEdits`. Operational.
- **`ai-title`**: AI-generated session title. Privacy-sensitive; ignored.
- **`system`**: subtype + duration + message count. Diagnostic; ignored for cost.
- **`pr-link`**: PR linking events with `prNumber`, `prUrl`, `prRepository`. Operational.

## Observed models

Across all 13 files in the audit (18,175 `assistant` events):

| Model              | Event count | Notes |
|--------------------|-------------|-------|
| `claude-opus-4-6`  | 8,761       | In pricing config |
| `claude-opus-4-7`  | 6,293       | In pricing config |
| `claude-sonnet-4-6`| 3,096       | In pricing config |
| `<synthetic>`      | 25          | Placeholder — see below |

### The `<synthetic>` model

25 events have `message.model == "<synthetic>"`. In observed cases,
`usage.input_tokens` and `usage.output_tokens` are both 0 and
`stop_reason` is `"stop_sequence"`. These appear to be internal
placeholder events (e.g., tool result echoes or stub messages) rather
than real API calls.

**Behavior:** the parser passes them through unmodified. The cost engine
returns `CostResult.ModelNotFound("<synthetic>", at)` because no
`<synthetic>` model exists in `pricing.json`. Downstream aggregators can
filter these out by inspecting the model id or by treating
`ModelNotFound` as a known-zero case for this specific model. **Phase 3
does not special-case `<synthetic>`.**

## Privacy rules (mandatory)

The parser:

1. **Reads** token counts, model id, timestamps, request ids, session ids, pricing-modifier fields (`service_tier`, `inference_geo`, `speed`), `cwd`, `gitBranch`, Claude Code version, and stop-reason metadata.
2. **Never reads** `content[].text`, `content[].thinking`, `content[].input` (tool input), `content[].content` (tool result), `attachment.content`, `attachment.prompt`, `lastPrompt`, `aiTitle`, `file-history-snapshot.snapshot`.
3. **Never holds** any read content in a parsed record. Privacy-sensitive fields are skipped at the deserializer layer.
4. The OTEL pipeline (Phase 4+) exports only token counts, model ids, timestamps, cache stats, and session ids. No content payloads ever leave the host.

## Edge cases the parser must tolerate

| Case | Behavior |
|---|---|
| Line that is not valid JSON | Skip with warning. Continue from next line. |
| Line whose `type` is unknown | Skip silently. |
| `assistant` event missing `message.usage` | Skip with warning (cost would be undefined). |
| `assistant` event with `<synthetic>` model | Parse normally; cost engine handles via `ModelNotFound`. |
| File open for writing (live session) | Open with `FileShare.ReadWrite \| FileShare.Delete`; tolerate `IOException` and retry once with a short back-off. |
| Partial last line (no terminating `\n`) | Skip the partial line with a warning. |
| `cache_creation` object missing entirely | Default both 5m and 1h to 0; usage is still computable. |
| Empty file | Yield zero entries; not an error. |
| Unknown future fields in `usage` | Ignored (System.Text.Json default). |

## What Phase 3 does NOT do

- No aggregation. The parser yields `LogEntry` records; aggregation
  by session / day / week lives in the collector (Phase 5).
- No OTEL emission. That's Phase 4.
- No hot-reload of session files. Phase 5 wires `FileSystemWatcher`
  for the project directory.
- No filtering by date range or model. Pure parse; consumer filters.
- No de-duplication. The parser surfaces `(sessionId, requestId)` and
  marks duplicates with the `IsDuplicate` flag based on a per-session
  observed-requests set, but does not drop them — that's an aggregator
  decision.

---

# Phase 5: Collector host

The collector is a .NET Worker Service that ties together the Phase 3
parser, the Phase 2 cost engine, and the Phase 4 metrics. Two
configuration files: `tokenscope.yaml` (the collector's own settings)
and `pricing.json` (the cost engine's rate table, hot-reloadable from
Phase 2).

## `tokenscope.yaml`

See [`config/tokenscope.example.yaml`](../config/tokenscope.example.yaml)
for the canonical reference. Key behaviours:

- `schema_version` must be `1`. Loader fails fast on other values.
- Path fields accept `null` (auto-detect to a platform default) or an
  explicit absolute path. **Validation differs**: `null` is
  permissive (warn + watch); explicit is strict (fail-fast if the
  directory doesn't exist).
- `initial_scan_max_age_days: 30` is the default. Set to `null` to
  remove the age limit on fresh installs that want full history.
- Unknown keys are rejected with a full-path error
  (`Unknown configuration key 'session_logs.scan_recursive'`).
- `subscription_mode` affects display labels only. The collector
  emits it as the OTEL resource attribute
  `tokenscope.subscription_mode` so Grafana and the CLI can read it
  from the metrics stream rather than re-parsing config.

### OTLP advanced configuration via env vars

`tokenscope.yaml.otlp` only carries `endpoint` and `protocol` — the
common-case fields. Authentication headers, TLS certificates, and
timeouts are configured via standard OTEL environment variables that
the OTEL .NET SDK reads automatically. Worked examples:

```sh
# Bearer-token auth to a managed OTEL collector.
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer ${TOKEN}"

# Self-signed TLS cert for the receiver.
export OTEL_EXPORTER_OTLP_CERTIFICATE=/etc/ssl/certs/my-otel-collector.pem

# Increase the default export timeout.
export OTEL_EXPORTER_OTLP_TIMEOUT=30000
```

Documenting these in YAML would duplicate OTEL's own configuration
surface. Single source of truth.

## State file (`seen.json`)

Persisted at `state.path/seen.json` (default
`$HOME/.tokenscope/state/seen.json`). One entry per session file the
collector has processed at least one line from.

```json
{
  "schema_version": 1,
  "files": [
    {
      "path": "/Users/q/.claude/projects/-Users-q-foo/abc-uuid.jsonl",
      "last_modified_utc": "2026-05-14T02:30:11.123Z",
      "byte_offset": 1827392,
      "last_processed_line_number": 9421
    }
  ]
}
```

### Resume rules

On startup, the collector evaluates each entry in `seen.json`:

| Condition | Action |
|---|---|
| File missing | Drop the entry. No work needed. |
| File length `<` saved `byte_offset` | File was truncated or replaced. Warn and full rescan. |
| File `LastWriteTimeUtc` differs from saved `last_modified_utc` | Treat as stale. Warn and full rescan. (Conservative — we don't trust mtime drift to mean "appended only".) |
| Otherwise | Resume reading at `byte_offset`. |

A startup log line summarises the rebuild:
`Resumed N files from state; M files require full rescan; dedup set starts empty.`

### Atomicity

Every state save writes to `seen.json.tmp` first, then
`File.Move(...,_overwrite: true)`. A crash mid-write cannot corrupt
the destination file — the worst case is the previous good state
remains.

If the state file is unreadable (corrupt JSON or I/O error), the
collector logs the failure and treats state as empty. The result is
"more work" (full rescan of every file), not "incorrect work" — every
recovery path is safe.

### Dedup across restart

The in-memory `(sessionId, requestId)` dedup set is **per-process**.
After restart it starts empty. Resume-by-`byte_offset` guarantees we
don't re-read lines we've already processed, so we don't re-see
already-counted `requestId`s — no cross-restart double-count concern.

At the Prometheus layer, counter resets at process restart are
detected by Prometheus's standard reset-detection logic; aggregations
remain correct.

## Byte-exact offset advancement

The coordinator reads the file byte-by-byte (8 KB buffer) and only
advances `byte_offset` past a confirmed `\n`. A partial last line
(no terminating newline) is **not** processed and the offset stays
at the position right after the last seen `\n`. On the next pass
(triggered by the FileSystemWatcher), the partial line will either
be complete (newline arrived since) and processed, or remain partial
and be skipped again.

## `FileSystemWatcher` debouncing

`FileSystemWatcher` fires on the session-logs root with
`IncludeSubdirectories = true` and filter `*.jsonl`. Events go
through a per-file debounce (250 ms) before reaching the work queue
to absorb the "burst of Changed events per save" that the watcher
emits on many platforms.

The processing path is single-consumer (one `Channel<string>`
reader). All file processing serialises through one in-process lock,
so per-file ordering is deterministic and the dedup HashSet doesn't
need its own lock.

## Graceful shutdown

`IHostApplicationLifetime` → `BackgroundService.StopAsync` →
- complete the channel writer
- disable + dispose the FileSystemWatcher
- wait for in-flight `ProcessFile` to finish (it holds the
  processing lock)
- flush state file one last time

A second `Ctrl-C` accelerates shutdown via the standard host pattern.

## Configuration precedence

`Program.cs` adds configuration providers in this order:

1. Built-in defaults from `Host.CreateApplicationBuilder` (host environment, no-prefix env vars, command line)
2. `tokenscope.yaml` via `AddYamlFile`
3. Env vars with prefix `TOKENSCOPE_` via `AddEnvironmentVariables("TOKENSCOPE_")`

Later providers win. So **`TOKENSCOPE_*` env vars override YAML, which overrides defaults**. The prefix scopes the override to tokenscope keys so unrelated env vars can't accidentally clobber config.

Convention:

```
TOKENSCOPE_<section>__<key>[__<subkey>]=value
```

Examples:

| Env var | Sets |
|---|---|
| `TOKENSCOPE_OTLP__ENDPOINT=http://otel-collector:4317` | `otlp.endpoint` |
| `TOKENSCOPE_SESSION_LOGS__PATH=/data/claude-logs` | `session_logs.path` |
| `TOKENSCOPE_STATE__PATH=/data/state` | `state.path` |
| `TOKENSCOPE_PRICING__CONFIG_PATH=/data/config/pricing.json` | `pricing.config_path` |

Double underscore is the .NET configuration separator. Keys are case-insensitive on Windows; portable code uses lower-case.

If you're debugging "why isn't my YAML change taking effect?", check for a `TOKENSCOPE_*` env var or command-line `--Section:Key=value` override. The collector logs the resolved paths at startup so the effective configuration is visible.

---

# Phase 6: Docker receiver stack

## Architecture

All four services run in a single `docker compose` stack on an internal network. Only Grafana publishes a port to the host.

```
┌──────────────────────────────────────────────────────────────────────┐
│ HOST                                                                 │
│                                                                      │
│  ~/.claude/projects/      ./config/pricing.json                      │
│         │ (ro bind mount)        │ (ro bind mount)                   │
│         │                        │                                   │
└─────────┼────────────────────────┼───────────────────────────────────┘
          ▼                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ docker compose — network: tokenscope                                 │
│                                                                      │
│   tokenscope-collector  ──▶  otel-collector  ──▶  prometheus         │
│      :reads /data/...         :4317 gRPC          :9090 UI           │
│      (no published ports)     :4318 HTTP                             │
│                               :8889 prom export ──┘                  │
│                               :8888 self-metrics                     │
│                                                                      │
│   tokenscope-state (named volume) → /data/state on the collector     │
│   prometheus-data (named volume) → /prometheus                       │
│   grafana-data (named volume) → /var/lib/grafana                     │
│                                                                      │
│   Only grafana :3000 is published to the host.                       │
└──────────────────────────────────────────────────────────────────────┘
```

One command — `docker compose up` — starts the whole pipeline.

## Volume mount strategy

| Mount target in container | Source on host | Mode | Why |
|---|---|---|---|
| `/data/claude-logs` | `${HOME}/.claude/projects` | `ro` (bind) | Read-only by construction. The collector reads logs, never writes back. The mount being read-only is a defense-in-depth guarantee. |
| `/data/state` | `tokenscope-state` named volume | `rw` | Resume state survives container restarts. Backup-friendly. The collector is the only writer. |
| `/data/config/pricing.json` | `./config/pricing.json` | `ro` (bind) | Hot-reload by `FileSystemWatcher` from Phase 2 still works over a bind mount (verified during Phase 6 smoke test). |

## `cwd` is an opaque identifier, never a path to resolve

Claude Code session log events include a `cwd` field with the project's absolute path on the host (e.g. `/Users/q/Documents/foo`). Inside a container, that path **does not exist**.

The collector treats `cwd` as opaque metadata — useful for session grouping, dedup keying, and as a metric attribute — but **never calls `File.Open(cwd)` or any other path-resolving API on it**. Grep the codebase for `cwd` and confirm: every use is either property access on `ParsedAssistantEvent`, or string emission to OTEL. No `Path.GetFullPath`, no `Directory.Exists`, no I/O. This design predates containerization (Phase 3 already treated `cwd` as opaque); Phase 6 only required documenting it.

## UID model: fixed non-root, world-readable logs

The container runs as UID 10001, a non-root user owned by the image. The bind-mounted `~/.claude/projects` is read using whatever world-bit (`o+r`) permissions the host filesystem provides — which on macOS and most Linux distributions is the umask default.

This avoids host-UID-mapping gymnastics that don't translate cleanly between macOS Docker Desktop and Windows Docker Desktop / WSL2. The cost is a documented assumption: if a user has tightened permissions on `~/.claude/projects`, they'll see "permission denied" on log reads. The troubleshooting doc covers the fix (`chmod -R o+rX ~/.claude/projects`).

State files inside the container live on a named volume; named volumes are created with container-user ownership on first run, so the write path always works.

## Cross-platform `${HOME}` resolution

`docker-compose.yml` references `${HOME}/.claude/projects` for the logs bind mount.

- **macOS Docker Desktop**: `${HOME}` resolves to the user's home (`/Users/q`). Native bind-mount semantics via gRPC FUSE.
- **Linux**: same as macOS, native bind-mount.
- **Windows Docker Desktop with WSL2**: `${HOME}` must resolve to the **WSL2 user's home** (`/home/q`), not the Windows user profile (`C:\Users\q`). Run `docker compose` from within a WSL2 shell, not from PowerShell. If running from PowerShell, set `HOME` explicitly to the WSL path. The `.env.example` documents this.

## FileSystemWatcher on macOS bind mounts

`FileSystemWatcher` in a Linux container uses `inotify`. On a macOS bind-mounted directory (Docker Desktop emulates the host filesystem via gRPC FUSE), `inotify` events may not fire reliably across all FUSE drivers.

Phase 6 smoke-test result determined the configuration of this fallback. See **Phase 6 deviations** in the PR for the verified behavior; the `session_logs.use_polling: true|false` configuration flag is available if `inotify` proves unreliable on macOS. Linux native and Windows WSL2 typically don't need polling — `inotify` works directly on those.

## Image pin selection (Phase 6)

The receiver stack pins three images. Selection criteria:

| Image | Pinned tag | Rationale |
|---|---|---|
| `mcr.microsoft.com/dotnet/sdk` (build stage) | `8.0.421-bookworm-slim` | Current 8.0.x SDK, Debian-based. Compatible with `global.json` 8.0.100 `latestFeature`. |
| `mcr.microsoft.com/dotnet/runtime` (runtime stage) | `8.0.27-bookworm-slim` | Current 8.0.x runtime, Debian-based. Image size ~80 MB. **Alpine deferred to Phase 9** — `musl` libc adds an unnecessary risk class for v1. |
| `otel/opentelemetry-collector-contrib` | `0.151.0` | 2-week-old release at pin time. OTEL Collector ships every ~2 weeks; pinning the immediately-prior release rather than HEAD trades 14 days of bleeding-edge for production hardening. |
| `prom/prometheus` | `v3.5.3` | **Prometheus v3.5 is the LTS line**, matching tokenscope's overall "stable, predictable" stance (cf. .NET 8 LTS, Microsoft.Extensions.Hosting 8.0.1). |
| `grafana/grafana` | `12.4.3` | Last release on the 12.x line. Grafana 13 is safe for tokenscope's specific use case but introduces a **unified storage migration** on first start that is one-way. See `troubleshooting.md` for the upgrade path. |

**Combined effect:** the entire stack pins to "mature, settled" versions rather than mixing one bleeding-edge component. Dashboard development in Phase 7 lands against a stable base.

## Pipeline shape

```
OTLP gRPC/HTTP receivers (4317/4318)
        │
        ▼
   batch processor              ← smooths bursty ingest for cumulative scrape view
        │
        ▼
attributes/from_resource        ← copies tokenscope.subscription_mode to a label
        │
        ▼
   prometheus exporter (8889)   ← Prometheus scrapes here
```

Prometheus also scrapes the OTEL Collector's **own** internal self-metrics on port 8888 (under job name `otel-collector-internal`), so the stack-health dashboard can show pipeline health independent of tokenscope data flow.

## Metric naming translation

OTEL → Prometheus name rewrites happen at the OTEL Collector's prometheus exporter:

| OTEL name | Prometheus series |
|---|---|
| `tokenscope.tokens.input` (Counter) | `tokenscope_tokens_input_total` |
| `tokenscope.cost.usd` (Counter) | `tokenscope_cost_usd_total` |
| `tokenscope.cache.hit_ratio` (Gauge) | `tokenscope_cache_hit_ratio` |
| `tokenscope.sessions.active` (Gauge) | `tokenscope_sessions_active` |

Rules: dots → underscores, monotonic counters get a `_total` suffix, gauges keep their name. Attribute keys (`model`, `session_id`, `component`, `ttl`) translate to Prometheus labels of the same name.

## Phase 6 stack-health dashboard

Provisioned at `docker/grafana/dashboards/stack-health.json`. Five panels covering scrape state of both Prometheus jobs, OTLP receive rate, distinct `tokenscope_*` series count, and last-scrape duration. Not the real product — ships with Phase 6 so "does `docker compose up` produce a working pipeline?" has a one-glance answer. Real per-domain dashboards arrive in Phase 7.

## Image pin selection (Phase 6)

The receiver stack pins three images. Selection criteria:

| Image | Pinned tag | Rationale |
|---|---|---|
| `otel/opentelemetry-collector-contrib` | `0.151.0` | 2-week-old release at pin time. OTEL Collector ships every ~2 weeks; pinning the immediately-prior release rather than HEAD trades 14 days of bleeding-edge for production hardening. |
| `prom/prometheus` | `v3.5.3` | **Prometheus v3.5 is the LTS line**, matching tokenscope's overall "stable, predictable" stance (cf. .NET 8 LTS, Microsoft.Extensions.Hosting 8.0.1). v3.11.x rolling-stable is the alternative for users who want the absolute latest. |
| `grafana/grafana` | `12.4.3` | Last release on the 12.x line. Grafana 13 is safe for tokenscope's specific use case (no breaking changes to provisioning file format or dashboard JSON v1 — verified) but introduces a **unified storage migration** on first start that is one-way: rollback to v12 requires a volume restore. Starting on v12 lets users opt into v13 as a deliberate later upgrade rather than committing the whole user base to the new storage format on first run. See `troubleshooting.md` for the upgrade path. |

**Combined effect:** the entire receiver stack pins to "mature, settled"
versions rather than mixing one bleeding-edge component. This makes
dashboard development in Phase 7 simpler — any oddity is a tokenscope
issue, not a "is this version-specific behavior?" issue.

## Pipeline shape

```
OTLP gRPC/HTTP receivers (4317/4318)
        │
        ▼
   batch processor              ← smooths bursty ingest for cumulative scrape view
        │
        ▼
attributes/from_resource        ← copies tokenscope.subscription_mode to a label
        │
        ▼
   prometheus exporter (8889)   ← what Prometheus scrapes
```

Prometheus also scrapes the OTEL Collector's **own** internal
self-metrics on port 8888 (under job name `otel-collector-internal`),
so the stack-health dashboard can show pipeline health independent of
tokenscope data flow.

## Metric naming translation

OTEL → Prometheus name rewrites happen at the OTEL Collector's
prometheus exporter:

| OTEL name | Prometheus series |
|---|---|
| `tokenscope.tokens.input` (Counter) | `tokenscope_tokens_input_total` |
| `tokenscope.cost.usd` (Counter) | `tokenscope_cost_usd_total` |
| `tokenscope.cache.hit_ratio` (Gauge) | `tokenscope_cache_hit_ratio` |
| `tokenscope.sessions.active` (Gauge) | `tokenscope_sessions_active` |

Rules: dots → underscores, monotonic counters get a `_total` suffix,
gauges keep their name. Attribute keys (`model`, `session_id`,
`component`, `ttl`) translate to Prometheus labels of the same name.

## Phase 6 stack-health dashboard

Provisioned at `docker/grafana/dashboards/stack-health.json`. Five
panels:

1. **`up{job="tokenscope"}`** — Prometheus scrape state for the OTLP-to-Prometheus exporter.
2. **`up{job="otel-collector-internal"}`** — Prometheus scrape state for the OTEL Collector's self-metrics.
3. **OTLP metric points received (rate / s)** — `rate(otelcol_receiver_accepted_metric_points_total[1m])` and the `_refused_` companion. Non-zero accepted with zero refused = pipeline healthy.
4. **Distinct tokenscope metric series count** — proves the OTEL pipeline rewrites our names correctly and Prometheus has indexed them.
5. **Last scrape duration for the tokenscope job** — sanity check.

This dashboard was the Phase 6 deliverable. Phase 7 deleted the file
and folded its panels into the unified `tokenscope.json` as a
collapsed "Stack health" row.

---

# Phase 7: Unified dashboard

## Single dashboard, seven collapsible rows

CLAUDE.md originally specified four dashboards (Overview, Sessions,
Cache efficiency, Cost breakdown). Phase 7 consolidates into one
unified `tokenscope.json` with collapsible rows. Rationale:

1. **One URL to share.** Send one link to the team; everyone sees the
   same context.
2. **Cross-section context.** A high-cost session visible in the
   Sessions row can be scrolled up to see its impact on the headline
   cost stats without switching pages.
3. **Reduced cognitive overhead.** The mental model is "tokenscope,"
   not "tokenscope-overview vs tokenscope-sessions vs ..."
4. **Easier maintenance.** One JSON file, one provisioning entry, one
   place to apply colour-palette decisions.
5. **Performance is fine.** Grafana 12 renders collapsed rows lazily,
   so a 20+ panel dashboard organised into rows performs the same as
   four separate dashboards.

Row layout:

| Row | Default | Content |
|---|---|---|
| Mode | expanded | Subscription mode banner (from `tokenscope.subscription_mode` resource attribute) |
| At a glance | expanded | Cost 24h / 7d / 30d, Saved by caching, Active sessions, Cache hit ratio |
| Cost trends | expanded | Cumulative cost by model (timeseries), Cost share by component (donut) |
| Sessions | expanded | Recently active sessions table, Top sessions by cost (history) |
| Cache efficiency | expanded | Cache hit ratio per session, Cache read vs cache write tokens |
| Cost breakdown detail | collapsed | Cost by model donut, Daily trend with 7-day MA, Token volume by model bar |
| Stack health | collapsed | UP/DOWN indicators, OTLP receive rate, distinct series, scrape duration |

## Cumulative cost — counter-reset-safe

The Phase 6 dashboard's "cumulative cost" panel queried the raw
counter, which produces a sawtooth when the OTEL Collector restarts
(Prometheus's reset detection drops the line to zero).

Phase 7 uses `increase(...[$__interval])` (reset-aware) and applies
Grafana's **Cumulative Sum** field transformation to produce a
monotonically non-decreasing line:

```promql
sum by (model) (increase(tokenscope_cost_usd_total[$__interval]))
```

Documented in the panel description. **Don't "fix" this back to a raw
counter query** — it'll regress reset handling.

## Color palette

| Role | Hex | When used |
|---|---|---|
| Cost (default) | `#8E8E93` neutral | Default cost stats |
| Savings | `#39A974` green | "Saved by caching" — higher is universally better |
| Activity / informational | `#4E92E0` blue | Counts, request totals, active sessions |
| Status: UP | `#39A974` | Binary health states only |
| Status: DOWN | `#E5484D` | Binary health states only |
| Time series | Grafana classic palette | Auto-assigned per series |
| Pie/donut | Grafana classic palette | Re-used so a model's colour stays stable across panels |

**No threshold coloring on cache hit ratio**, throughput, or any ratio
that lacks a universal "correct" value. The dashboard shows the
number; the user judges.

## Theme

Dashboards are designed for **dark theme**. They render on light theme,
but neutral `#8E8E93` loses contrast on a white background. README
recommends dark theme. A light-theme variant may ship in a future polish
phase if requested.

## Project name derivation

The `project` / `project_name` metric labels are derived from each
event's `cwd` field in the Collector:

```
cwd = "/Users/q/Documents/RiderProjects/tokenscope"
  ↓
project      = "-Users-q-Documents-RiderProjects-tokenscope"   (encoded; lossless)
project_name = "tokenscope"                                    (friendly; last segment)
```

`/` and ` ` both become `-`, matching Claude Code's own
directory-name encoding. The encoded form is unique per host
filesystem. `project_name` is friendly but may collide between
projects with the same final directory name (e.g.
`~/work/tokenscope` and `~/personal/tokenscope`); use `project` to
disambiguate when needed.

Both labels are sanitized: control characters stripped, ≤ 64 chars
(truncated with `...` if longer). Missing `cwd` → both labels =
`"unknown"`.

## Scope: Claude Code only

tokenscope tracks **Claude Code session logs only**. This is a
deliberate constraint baked into the project from Phase 3 (the audit
that defined the parser schema) and stated in CLAUDE.md's
architectural decisions table ("Data scope: Claude Code local session
logs only").

What this excludes:

- **claude.ai web / desktop conversations.** Stored server-side, not
  in any local filesystem location tokenscope can read. Capturing
  these would require integration with Anthropic's Admin API, which
  is only available on Enterprise plans and was explicitly listed as
  out of scope in CLAUDE.md ("no API integration, no Admin API, no
  remote sources").
- **Anthropic API calls from user code.** Direct SDK calls don't go
  through Claude Code and produce no `~/.claude/projects/` log
  entries.

For users who split work between Claude Code and claude.ai
web/desktop, the dashboard under-counts. The mode-banner row makes
this explicit ("Claude Code sessions only — claude.ai web/desktop
not tracked") so users don't read $X as their full Claude spend.

A future v0.2+ could add Admin API integration as an opt-in
collector mode for Enterprise users. v0.1.0 deliberately stays
local-first to honour the privacy and zero-network-call constraints.

## Editing dashboards: code, not UI

Grafana's provisioning is read-only for the panel JSON. **Edits in the
Grafana UI do not persist across container recreation.** For ad-hoc
exploration the UI is fine — for changes that should survive
`docker compose down`:

1. Edit the dashboard in the UI as needed.
2. **Share → Export → Save to file** (or use the JSON Model editor).
3. Replace `docker/grafana/dashboards/tokenscope.json` with the
   exported file.
4. Commit to git.

Provisioning picks up changes within 30 s (the `updateIntervalSeconds`
in `dashboards.yaml`); reloading the dashboard in the browser after
committing shows the new state. Also documented in
`troubleshooting.md`.

