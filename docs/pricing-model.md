# Pricing model

This document explains how tokenscope computes the cost of a Claude API request from
token counts, what the pricing config looks like, and how hot-reload preserves
historical correctness.

## TL;DR

For each request, tokenscope looks up the model rate that was effective at the
**request's timestamp** (not the current time), then computes:

```
cost_input        = (input_tokens         / 1_000_000) × input_per_mtok
cost_output       = (output_tokens        / 1_000_000) × output_per_mtok
cost_cache_read   = (cache_read_tokens    / 1_000_000) × cache_read_per_mtok
cost_cache_5m     = (cache_write_5m_tokens / 1_000_000) × cache_write_5m_per_mtok
cost_cache_1h     = (cache_write_1h_tokens / 1_000_000) × cache_write_1h_per_mtok

total = cost_input + cost_output + cost_cache_read + cost_cache_5m + cost_cache_1h
```

All arithmetic is performed with `decimal` to avoid floating-point rounding on
money. Token counts are `long`.

## Pricing config

`config/pricing.json`:

```json
{
  "schema_version": 1,
  "models": [
    {
      "id": "claude-opus-4-7",
      "rates": [
        {
          "effective_date": "2026-01-01T00:00:00Z",
          "input_per_mtok":         5.00,
          "output_per_mtok":       25.00,
          "cache_read_per_mtok":    0.50,
          "cache_write_5m_per_mtok": 6.25,
          "cache_write_1h_per_mtok": 10.00
        }
      ]
    }
  ]
}
```

- `schema_version` must be `1`. tokenscope rejects any other value.
- `models[*].rates` is a list of rate entries with an `effective_date`. The
  cost lookup picks the entry with the latest `effective_date` that is `<=` the
  request timestamp.
- All rates are USD per million tokens (MTok). All `effective_date`s are stored
  as UTC.

### Validation rules (loader)

The loader rejects a pricing config that violates any of the following — and
collects **all** violations into a single `PricingValidationException`:

- `schema_version` is not the supported version.
- `models` is missing or empty.
- A model's `id` is missing or duplicated.
- A model's `rates` list is missing or empty.
- Any `effective_date` is in the future (relative to the loader's clock).
- Two rate entries for the same model have the same `effective_date`.
- Any per-MTok rate is missing or negative.

### Cache pricing multipliers

Anthropic's prompt-caching pricing is built on three multipliers relative to
the model's base input rate. tokenscope encodes the resulting absolute rates
in `pricing.json` and does **not** rederive them at runtime, but the
multipliers are useful for sanity-checking the config:

| Operation         | Multiplier of input rate |
|-------------------|--------------------------|
| Cache write (5m)  | 1.25×                    |
| Cache write (1h)  | 2×                       |
| Cache read (hit)  | 0.1×                     |

For Opus 4.7 with `input_per_mtok = 5.00`:
- `cache_write_5m_per_mtok` should be `5.00 × 1.25 = 6.25` ✓
- `cache_write_1h_per_mtok` should be `5.00 × 2 = 10.00` ✓
- `cache_read_per_mtok` should be `5.00 × 0.1 = 0.50` ✓

## Worked examples

All examples use **Claude Opus 4.7** rates as captured 2026-05-13:

| Component        | Rate per MTok |
|------------------|---------------|
| Input            | $5.00         |
| Output           | $25.00        |
| Cache read       | $0.50         |
| Cache write 5m   | $6.25         |
| Cache write 1h   | $10.00        |

### Example 1 — pure input request

A request consumes 200,000 input tokens and produces no output (or, more
realistically, the request is being scored *only on its input cost*).

| Component   | Tokens   | Math                                  | Cost     |
|-------------|----------|---------------------------------------|----------|
| Input       | 200,000  | (200,000 / 1,000,000) × $5.00         | $1.000   |
| Output      | 0        | 0                                     | $0.000   |
| Cache read  | 0        | 0                                     | $0.000   |
| Cache 5m    | 0        | 0                                     | $0.000   |
| Cache 1h    | 0        | 0                                     | $0.000   |
| **Total**   |          |                                       | **$1.000** |

### Example 2 — cache write at 5-minute TTL

The first request in a conversation seeds a 5-minute cache with 80,000 tokens
and produces a 1,000-token output.

| Component   | Tokens  | Math                                       | Cost     |
|-------------|---------|--------------------------------------------|----------|
| Input       | 0       | (uncached input is 0 here; everything went into the cache write) | $0.000 |
| Output      | 1,000   | (1,000 / 1,000,000) × $25.00               | $0.025   |
| Cache 5m    | 80,000  | (80,000 / 1,000,000) × $6.25               | $0.500   |
| **Total**   |         |                                            | **$0.525** |

### Example 3 — cache write at 1-hour TTL

Same shape as example 2, but the 80,000 tokens are written to the 1-hour
cache instead. (1h TTL costs more on write but is valid for longer.)

| Component   | Tokens  | Math                                       | Cost     |
|-------------|---------|--------------------------------------------|----------|
| Output      | 1,000   | (1,000 / 1,000,000) × $25.00               | $0.025   |
| Cache 1h    | 80,000  | (80,000 / 1,000,000) × $10.00              | $0.800   |
| **Total**   |         |                                            | **$0.825** |

### Example 4 — cache read

A follow-up request reuses the cached system prompt (80,000 tokens), adds a
small new input (2,000 tokens), and produces a 500-token output.

| Component   | Tokens  | Math                                       | Cost     |
|-------------|---------|--------------------------------------------|----------|
| Input       | 2,000   | (2,000 / 1,000,000) × $5.00                | $0.010   |
| Output      | 500     | (500 / 1,000,000) × $25.00                 | $0.0125  |
| Cache read  | 80,000  | (80,000 / 1,000,000) × $0.50               | $0.040   |
| **Total**   |         |                                            | **$0.0625** |

Note: cache reads cost only 10% of base input. The same 80,000 tokens would
have cost `$0.400` if billed as fresh input — the cache saved $0.360 on this
single read.

### Example 5 — full mixed request

A request that uses every component: some fresh input, a 5-minute cache
write (new context), a 1-hour cache write (longer-lived context), a cache
read (hitting an earlier cache entry), and an output.

| Component   | Tokens   | Math                                        | Cost      |
|-------------|----------|---------------------------------------------|-----------|
| Input       | 10,000   | (10,000 / 1,000,000) × $5.00                | $0.050    |
| Output      | 2,000    | (2,000 / 1,000,000) × $25.00                | $0.050    |
| Cache read  | 50,000   | (50,000 / 1,000,000) × $0.50                | $0.025    |
| Cache 5m    | 30,000   | (30,000 / 1,000,000) × $6.25                | $0.1875   |
| Cache 1h    | 20,000   | (20,000 / 1,000,000) × $10.00               | $0.200    |
| **Total**   |          |                                             | **$0.5125** |

## Temporal correctness

The cost of a request is determined by **the rate that was effective at the
request's timestamp**, regardless of when the cost is computed.

This matters because pricing changes. If Anthropic adjusts Opus 4.7 from
$5/MTok to $6/MTok on 2026-08-01, the `pricing.json` file gains a new entry:

```json
{
  "id": "claude-opus-4-7",
  "rates": [
    { "effective_date": "2026-01-01T00:00:00Z", "input_per_mtok": 5.00, ... },
    { "effective_date": "2026-08-01T00:00:00Z", "input_per_mtok": 6.00, ... }
  ]
}
```

When tokenscope later computes the cost of a request that was made
2026-04-15, it picks the **first** entry (`effective_date 2026-01-01`,
`$5/MTok`). A request made 2026-09-01 picks the **second** entry. This holds
even if the lookup happens on 2026-12-25 — the cost engine never uses the
current time.

### Hot-reload behavior

`PricingTableProvider` watches the pricing config directory and reloads the
table when the file changes. Reload is atomic: in-flight cost calculations
either see the **old** snapshot (if they grabbed the table reference before
the swap) or the **new** snapshot (if after). They never see a torn,
half-loaded table.

The temporal-correctness rule above means there is no "race condition"
during a reload, **provided the new file preserves historical entries**.
If the old config had a `2026-01-01` rate and the new config also keeps
that entry, cost calculations for old requests still produce the same
answer.

The cost engine treats the rate file as a historical record, not as a
live rate sheet. To deprecate a rate, **add a new entry with a later
`effective_date`** — do not delete the old entry. If the new file omits
a historical rate, requests with timestamps before the earliest entry
return `CostResult.NoRateEffective`, a typed error.

If the new file is invalid (validation error or malformed JSON), the
old snapshot is retained and the failure is surfaced via the provider's
`onReload` callback as `PricingReloadEvent.ValidationFailed` or
`PricingReloadEvent.IoFailed`. The system never falls back to no
pricing — it falls back to the previous *good* pricing.

## Missing model handling

If a request references a model not present in the pricing table,
`CostCalculator.Calculate` returns
`CostResult.ModelNotFound(modelId, requestedAt)` — a typed error.
The calculator does **not** throw an unhandled exception, and does **not**
fall back to a default model rate. Callers decide how to surface the
error (log, fail the aggregation, drop the request, etc.).

Similarly, if the model is known but no rate is effective at the request
timestamp (e.g., a 2025 request against a config that starts at
2026-01-01), the calculator returns `CostResult.NoRateEffective`.

## Subscription mode (Pro, Max, Enterprise)

The cost engine itself is subscription-agnostic. It always computes the
"real" API-equivalent cost from token counts and public rates.

A separate layer — the CLI and Grafana dashboards — applies the
subscription banner / label. In subscription mode (`pro`, `max5x`,
`max20x`), the displayed cost is labelled "**Inferred (subscription is
flat-rate)**" so users do not mistake it for a real bill. Token counts
and cache efficiency metrics display identically across modes; only the
cost label changes.

This separation keeps cost math testable and subscription handling
isolated to the presentation layer.

## Unit conventions

- **Token counts:** `long`. Maximum representable count is ~9.2 × 10^18,
  far above any realistic Claude API workload.
- **Costs:** `decimal`. Provides 28–29 digits of precision and an exponent
  range of ±10^28 — fits any realistic invoice with cent-level precision
  intact.
- **Timestamps:** `DateTimeOffset` stored as UTC. Display conversion to
  the user's local zone happens at the CLI / Grafana boundary, never in
  the cost engine.
- **Currency:** USD only at v1. The pricing loader **fails fast** if
  `currency` is set to anything other than `"USD"` (case-insensitive),
  with the error `currency '<X>' is not supported (only USD is
  supported in this version).` A null or missing `currency` field is
  treated as USD for backward compatibility. There is no FX
  conversion, no exchange-rate lookup, and no multi-currency
  aggregation anywhere in tokenscope. The OTEL `tokenscope.cost.usd`
  metric uses `USD` as its unit and is not localized; presentation
  formatting (`$`, decimal places) is a dashboard concern (Phase 7).
