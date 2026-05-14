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
