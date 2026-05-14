# Dashboard QA checklist

Phase 9 shipped this checklist after multiple panel-correctness
regressions slipped through the Phase 7 "all 14 items shipped"
summary. The Phase 7 verification was end-to-end-pipeline-correct
(metrics flow, cost totals math) but did not validate **what each
panel actually displays** against the real corpus.

Use this checklist for every PR that touches
`docker/grafana/dashboards/tokenscope.json` or any of the metric
emission paths.

## How to QA the dashboard

1. **Bring up the full stack** with real data:
   ```sh
   cd docker
   docker compose up -d --build
   ```
   Wait for the collector to do its initial scan. The dashboard at
   <http://localhost:3000/d/tokenscope/tokenscope> needs at least one
   Prometheus scrape cycle (~15s) before panels populate.

2. **Verify each row against direct Prometheus queries.** Don't trust
   the panel to be correct — verify the underlying data shape with
   `docker exec tokenscope-prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=<promql>'`
   and check that the panel matches.

3. **Walk every panel.** Don't skim. Each panel has a question it's
   meant to answer and a math check below.

## Checklist by row

### Mode banner

- [ ] Shows the actual subscription mode from the OTEL resource
      attribute, not "unknown" (unless no data has been collected
      yet).
- [ ] The "Claude Code sessions only" scope note is present.
      Phase 9 added this after a scope-honesty review concluded
      "Claude usage" was misleading.

### At a glance

For each stat:

- [ ] **Cost — last 24h / 7d / 30d** — three panels must return
      different values when you have ≥ 7d of history. **Verify the
      PromQL inside each panel uses a fixed window (`[24h]` / `[7d]` /
      `[30d]`), NOT `$__range`.** Phase 9 caught this regression:
      all three were using `$__range` and returning identical values.
- [ ] **Saved by caching** — math is `9 × cache_read_cost`.
      Hand-check by querying
      `sum(tokenscope_cost_usd_total{component="cache_read"}) * 9`.
      The ratio (savings / total cost) maxes at 9 when 100% of cost
      is cache_read; a heavily-cached real workflow can legitimately
      hit ratio ≈ 8.
- [ ] **Active sessions** — counts sessions with activity in the
      last `active_session_window_minutes` (default 10). Should be
      the integer count from `tokenscope_sessions_active`.
- [ ] **Cache hit ratio** — displayed with **4 decimal places**. A
      long heavily-cached session legitimately produces 99.9997%;
      rounding to 1 decimal hides the truth and prompts users to
      think "100% is impossible." Verify the precision.

### Cost trends

- [ ] **Cumulative cost by model** — line should be monotonically
      non-decreasing through the dashboard time range. The Phase 7
      attempt used `increase()` + Grafana Cumulative Sum transform
      and produced a misleading spike. Phase 9 reverted to
      `sum by (model) (tokenscope_cost_usd_total)` (raw counter),
      accepting that the line drops on container restart.
- [ ] Legend's `lastNotNull` for each model = the line's right-edge
      value. **If the chart visually peaks higher than the legend
      total, the transformation chain is wrong.**
- [ ] **Cost share donut** — zero-value series filtered out.
      Hand-check by querying `sum by (component) (...)` and
      confirming the donut shows only non-zero components.

### Sessions

- [ ] **Recently active table** — one row per `session_id`. If you
      see the same UUID twice with different `project_name`s,
      something's wrong with the aggregation; the data is per
      session, not per (session, project). The query must
      `sum by (session_id)`, not `sum by (session_id, project_name)`.
- [ ] **Session column** shows truncated short (first 8 chars).
      Full UUID column adjacent for joining/filtering.
- [ ] **Cost column** is neutral display, no red/yellow/green
      gradient. Per the Phase 7 palette rule: no threshold
      colouring on values without universal "good/bad" ranges.

### Cache efficiency

- [ ] **Hit ratio per session table** — same per-session
      aggregation rule as above. **Plain number display, no
      gauge bar** (the gauge bar implies 100% is the goal state;
      it isn't).
- [ ] **Hit ratio shown with 4 decimals**, not 1.
- [ ] **Cache read vs cache write timeseries** — read rate and
      write rate (split by 5m / 1h ttl) both visible if data
      exists.

### Cost breakdown detail (collapsed)

- [ ] **Cost by model donut** — zero-value series filtered.
- [ ] **Daily cost (last 7 days)** — bar chart of `increase[24h]`
      buckets. **Don't render the 7-day moving average** if the
      data range is < 7 days (it computes meaningless values).
- [ ] **Input tokens by model bar** — horizontal bars, model name
      on the Y-axis. **Do not show "2026" or any year as the
      axis label** (that's Grafana misinterpreting an instant
      result as a time series; the panel needs `format: "table"`
      on the target and proper barchart config).

### Stack health (collapsed)

- [ ] **Both scrape jobs UP** (`tokenscope` and
      `otel-collector-internal`).
- [ ] **OTLP rate non-zero** when the collector is processing
      events.

## Pre-tag visual audit

Before tagging any release, open the dashboard in a browser, expand
every collapsed row, and walk this checklist. Take a fresh
screenshot for the README only after every box checks.

## Regression test (suggested for future Phase)

A unit/integration test that takes one synthesized representative
session JSONL, runs it through the full pipeline (parser → cost
engine → metric emission via a MeterListener), and asserts
expected metric values against hand-computed expectations. Would
have caught:

- Bug 1 (display precision) — would have caught the rounding
  in test output.
- Bug 2 (`$__range` vs fixed windows) — strictly a dashboard
  issue, not a code issue, so not catchable here.
- A future "parser misattributes cache_read to input" regression
  would be caught immediately.

Not in v0.1.0; flag for v0.2.
