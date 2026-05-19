# Backlog

Post-PLAN feature slices captured for later. Every entry below is
**discretionary** — PLAN.md §6 is fully delivered as of slice 17, and
the dashboard is feature-complete for its core "see what I'm spending
on Claude Code" purpose. These add depth, not coverage.

Each slice is independent — pick any subset in any order.

## Scope notes

- **No CI / release engineering.** This is a personal repo; we don't
  ship to Docker Hub / GHCR / PyPI / a release tag. Don't add those
  items back here without re-checking.
- Stop-for-go cadence still applies. One slice → one PR → merge → next.

## Slices

### Slice 18 — CSV / JSON export

**Scope.** `st.download_button` on every data-driven view (Overview,
Cache, Models, Day detail, Session detail). One CSV button + one JSON
button per surface, serialising the *current window's* data — the
exact data the charts are rendering.

**Why.** Most-asked feature for any analytics dashboard. Users want
to slice the numbers in their own tools (Excel, Sheets, notebooks).

**Effort.** Low — a few lines per view. No new analytics.
**Risk.** Low.

### Slice 19 — Anomaly flag

**Scope.** New `analytics.anomaly_score(daily_report, today)` returning
the standard-deviations-from-the-mean for today's cost across the
window. Render a small badge above the Overview KPIs when
`|score| >= 2`. Threshold tunable in `tokenscope.config.toml`.

**Why.** Cheap, surfaces a signal you couldn't see before: "today is
unusually high *for you*" — without needing to remember the prior
week's numbers.

**Effort.** Low — small pure-function helper + a single render line.
**Risk.** Low.

### Slice 20 — Sparklines on KPIs

**Scope.** Tiny inline trend chart under each top-level KPI card
(Window cost, Active-block $/hr, Cache hit ratio, etc.) — Plotly
mini-chart with axes off, ~60px height, ~7-day data.

**Why.** KPIs gain context without taking screen real estate. The
"is that number trending up or down?" question gets answered visually.

**Effort.** Low (Plotly) but needs care with the column flex from
slice 16's responsive layout — embedded charts shouldn't break the
`min-width: 220px` wrap rule.
**Risk.** Low-medium.

### Slice 21 — Weekly view

**Scope.** New top-level page (`?view=weekly`) using ccusage's
existing `weekly` command via `data.weekly(query)`. Reuses every
Overview chart structure with week buckets. Add `weekly` to
`TOP_LEVEL_VIEWS`.

**Why.** We already hit `ccusage weekly` (since slice 2) but never
render the result. Useful for users tracking week-over-week spend
instead of day-over-day noise.

**Effort.** Medium. New page, but most analytics functions can be
parameterised over the daily/weekly shape rather than rewritten.
Charts using `daily_report.daily[i].date` would need to read
`weekly_report.weekly[i].week` instead — small generalisation.
**Risk.** Medium.

### Slice 22 — Comparison mode

**Scope.** "Compare to..." sidebar control: a second date range,
plotted alongside the current window on the same axes. Difference
KPIs at the top of Overview ("vs. comparison window: +X%").

**Why.** Natural follow-on to the prior-period delta we already
compute. Answers "is this month better than last month" without
flipping tabs.

**Effort.** Medium-high. Sidebar gets a second range picker; URL
state expands to carry both ranges; chart builders need dual-trace
mode (probably an optional `comparison=` kwarg).
**Risk.** Medium-high — biggest of the five.

### Slice 23 — Budget tracking + spend alerts

**Scope.** Budgets entered through the sidebar UI, not buried in a
TOML. A new **"Budget"** section in the sidebar with three optional
number inputs:
- Daily budget (USD)
- Monthly budget (USD)
- Window budget (USD — applies to the currently-selected date range)

Empty fields = no budget for that period (the most common case).
Values persist to a small local file (`~/.config/tokenscope/budgets.toml`).
In Docker, this path needs to be a volume mount (same pattern as
`~/.claude` — add a line to the README's Docker run command).

`tokenscope.config.toml` may still set *defaults* (useful when a team
deploys the dashboard with a house budget), but the UI is the
primary path and always overrides.

When *any* budget is set, the Overview gains a **"Budget remaining"**
KPI card showing the positive — `$642.85 left of $1,000 (May)` — with
a progress bar visualising consumption. Sits alongside Window cost.

The same data drives a tiered alert banner when consumption climbs:
- 0–80% → silent. The Budget-remaining card is the only signal.
- 80–100% → yellow warning banner above KPIs.
- ≥100% → red over-budget banner + KPI flips to `$X over budget`.

Live view extends the check with the active block's burn rate
projected forward: "at this rate you'll hit your daily budget by
14:00". When running in Docker, mirror warnings to stdout
(`docker logs`) so users polling logs can also see it.

**Why.** Most users don't *want* a runaway alarm — they want to
glance at the dashboard and see how much room they have left in the
month. The positive framing ("$X left of $K") is the headline; the
alert is what happens when remaining → 0. Enterprise / pay-per-API
users on a fixed monthly cap especially want this.

**Effort.** Medium. Needs:
- Sidebar number-input section (3 fields, all optional)
- Persistence layer (`tokenscope.budgets.load() / save(budgets)`)
  writing to `~/.config/tokenscope/budgets.toml`. Mirror the existing
  `~/.cache/tokenscope/` pattern from `pricing.py`.
- Optional config-file defaults (`tokenscope.config.toml [budget]`)
  honoured when no UI-set value exists
- `analytics.budget_status(daily_report, today, budgets)` returning
  `{remaining, consumed, percent_used, tier: ok|warn|over}` for each
  active budget
- KPI card component (`Budget remaining`) with progress bar
- Banner component (`ui/_alerts.py`) for warn/over tiers
- Banner + KPI wired into Overview; banner repeated on Live
- Optional Live-view "ETA to limit" using the existing burn rate
- README update: add `~/.config/tokenscope:/root/.config/tokenscope`
  to the Docker run example so budgets persist across container
  restarts

**Risk.** Low-medium. Three tiers + opt-in via config (default no
budget = no card and no banner) keeps it out of the way until the
user opts in.

**Honest caveats** for whoever picks this up:
- This is *informational*, not enforcement. We don't intercept
  Claude Code's network calls or pause sessions. The banner is a
  loud "hey".
- Browser tab must be open / refreshed for the banner to appear.
  The Live view auto-refreshes every 30s; Overview reruns on
  sidebar interaction. A user with the tab closed sees nothing
  until they reopen.
- The Docker-logs mirror happens only when the script actually
  re-runs — Streamlit's rerun model isn't a daemon. Genuine
  background watchdog (long-running poller) would be a separate
  slice and isn't free.
- The flat-rate plan KPI flow (Pro / Max) already shows a fixed
  monthly fee. The budget card should only render on Enterprise,
  where the per-token spend actually varies; on flat-rate plans
  the user already knows what they'll pay.

### Slice 24 — Expensive-session forensics

**Scope.** A "Most expensive sessions this window" expander on the
Overview. Top-N (configurable, default 5) sessions ranked by cost.
Each row carries:

- Friendly session label (project + start time)
- Cost (USD)
- Total tokens
- **Token-mix shape** — a small inline stacked bar showing the
  input / output / cache_create / cache_read split for that session
  (the same primitive `session_token_mix` already renders on the
  Session-detail view, scaled down to ~80px wide). The user reads the
  shape; we don't moralise about it.
- **Low cache hit** tag (red dot) when
  `cache_read / (input + cache_create + cache_read) < 50%`. This is
  the *only* surviving heuristic tag — see "Why one tag, not five"
  below.
- An **Open** button that drills into the existing Session-detail
  view, where the full per-model donut and full-size token-mix bar
  live.

**Why one tag, not five (design lesson).** An earlier draft of this
slice flagged five reasons: *Output-heavy*, *Cache-write heavy*,
*Low cache hit*, *Heavy model*, *Large volume*. That design tripped
on its own example: a normal `read code → write doc → slice doc →
execute each slice` workflow is naturally both output-heavy AND
cache-create-heavy — exactly the shape the heuristics would flag as
"expensive for bad reasons" even though every token was load-bearing.
The heuristics were pretending to know which work is justified, which
we can't tell from the token mix alone.

The fix is to **drop the moralising tags, keep the data**:

- *Output-heavy* / *Cache-write heavy* / *Large volume* → all
  workflow-dependent. Removed. The inline token-mix bar shows the
  same shape without claiming the shape is wrong.
- *Heavy model* → too judgemental without per-task context. Removed
  (already flagged for v1 removal in the original draft).
- *Low cache hit* → **kept.** Workflow-independent: if the cache-read
  share of cache-eligible tokens is below 50%, you are literally
  paying for context that isn't being reused. That's a fixable infra
  signal (look for `/clear`, model swaps, big file diffs busting
  prefixes) regardless of whether you're writing docs or code or
  both.

**Why.** Headline cost numbers tell you *what*. This view points to
*which sessions* drove the spend and lets the user inspect the
shape. The user decides whether the shape is appropriate for the
task — we don't.

**Effort.** Low-medium. Needs:
- `analytics.expensive_sessions(session_report, n=5)` — pure-function
  ranker that returns `[{session, cost, tokens, mix: {input, output,
  cache_create, cache_read}, low_cache_hit: bool}, ...]`.
- A compact `mini_token_mix_bar(mix)` chart helper in `ui/charts.py`
  (could share the existing `session_token_mix` layout with smaller
  margins + no axis labels).
- UI table on Overview (Enterprise-only — same gating as the
  Cost-composition panel).
- Two knobs in `tokenscope.config.toml`:
  ```toml
  [forensics]
  low_cache_threshold = 0.50
  top_n = 5
  ```

**Risk.** Low. The single surviving tag is the one with the cleanest
"do this to fix it" reading, and the mini-chart approach is honest:
we surface the data, the user does the diagnosis.

### Sidebar Models default — three independent slices

The Models multiselect currently defaults to the full
`available_models(discovery_daily)` list on **first render only**.
After that, `st.session_state["sidebar-models"]` (or a shared URL's
`models=` param) takes over, so a user landing on
`?models=claude-opus-4-7` sees only that one model selected even
if other models also have data in the window. The dashboard then
shows numbers that look wrong but aren't, until the user notices
the filter and adds the missing models manually. We hit this exact
pain twice this audit cycle (the "$153 missing" investigation, the
"models confusing" feedback).

Slices 25–27 each take a different approach to the same pain
point. Pick one, or layer them — they don't conflict.

### Slice 25 — "Select all models" button

**Scope.** Add an explicit "Select all" button next to the Models
multiselect in the sidebar. Clicking it wipes
`st.session_state["sidebar-models"]` and the URL's `models=` param,
then reruns — the multiselect re-renders with its full default
list (`available_models(discovery_daily)`). Mirrors what
`_render_reset_button` does today for the broader reset.

**Why.** Smallest possible slice that addresses the pain. No
state-model changes. The user has a one-click escape hatch when
they realise the filter is narrower than they wanted.

**Effort.** Low. Single `st.button` + the same session-state
cleanup pattern `_render_reset_button` already uses (~15 lines
in `_render_models_multiselect`).
**Risk.** Low. Pure UI addition; no impact on the existing
selection persistence.

**Doesn't help.** The shared-URL-with-stale-models case is not
automatic — the user still has to *notice* the filter is narrow
and click the button.

### Slice 26 — Re-seed Models default on every render

**Scope.** Change the default behaviour so the multiselect treats
"every available model" as the implicit selection unless the user
has *explicitly* de-selected at least one model in this session.
Requires a parallel session_state flag
(`sidebar-models-user-narrowed: bool`) so we can distinguish
"user has touched this widget" from "this is the initial state".

URL `models=` param only seeds the multiselect when
`user-narrowed=True`. Otherwise (fresh open, shared link from a
narrowing session, etc.) the multiselect defaults to all available.

**Why.** Closes the shared-URL pain automatically — no user action
required. Common-case usefulness wins over strict
"explicit-intent-always-wins" semantics. The user who *did*
narrow their selection still gets their narrowing preserved.

**Effort.** Medium. New session_state flag; the URL-seed logic in
`_seed_session_from_url` and the multiselect render in
`_render_models_multiselect` both get a condition; tests need to
cover four state combinations (narrowed × url-present).
**Risk.** Medium. Two-axis state machine (selection list +
narrowed flag) is subtle. Shared URLs partially lose information
about what the originator had selected.

### Slice 27 — Auto-merge newly-discovered models

**Scope.** On every sidebar render, compare
`available_models(discovery_daily)` against the current
`selected_models`. Any model in the former but not the latter that
the user has never explicitly de-selected gets auto-added. Models
the user has explicitly removed (tracked in a separate
`sidebar-models-removed: set[str]`) stay removed.

**Why.** Handles the "new model version appeared mid-window" case
cleanly — a fresh `claude-opus-4-8` showing up in your data
automatically joins the selection without prompting you. Lower
surprise than slice 26's broader re-seed because it only ever
*adds*, never restores something you removed.

**Effort.** Medium. New `sidebar-models-removed` session_state set;
multiselect render diffs and merges; the Reset button needs to
clear both pieces of state.
**Risk.** Medium. Subtle interaction with date-range changes — a
user who narrowed by removing legacy models might see them
re-appear when scrolling into an older window. Mitigation: the
removed-set is keyed on model name, not window; explicit removal
sticks across windows.

**Why this isn't a bug fix.** Today's behaviour is *technically*
correct — explicit user intent (URL params, prior interaction)
beats defaults. These slices are UX improvements that trade
"explicit intent always wins" for "common-case usefulness wins".
Opinion changes, not defect fixes; hence backlog, not patches.

### Slice 28 — Anthropic Admin API ingestion (true client-source axis)

**Scope.** A second ingestion path, parallel to ccusage, that pulls
from the Anthropic Admin API
(`/v1/organizations/usage_report/messages`). New module
`tokenscope/admin_api.py`, new pydantic shapes for the response,
new Streamlit cache layer, new merge/dedup logic to reconcile
Admin API rows against ccusage rows that overlap. Surfaces in the
Daily view as a real `Agent` column (Claude Code / SDK / console /
third-party) replacing today's constant `Claude Code` chip.

**Why.** ccusage reads `~/.claude/projects/*/*.jsonl` — Claude Code
transcripts only. SDK / console / third-party API requests are
invisible to it. As long as ccusage is our sole ingestion boundary,
the Daily view's "agent" axis is structurally constant (every row is
Claude Code by construction) and the chip is informational rather
than discriminating. The Admin API observes **all** organization
traffic regardless of client, so this is the only path that yields
a real client-source breakdown.

**Effort.** **High.** New auth (admin-tier API key handling, distinct
from the existing per-user key), new module parallel to `ccusage.py`,
new pydantic shapes (`extra="forbid"`), new caching layer, new
merge/dedup logic — Admin API rows and ccusage rows will both report
the same usage when invoked from Claude Code, and the dashboard must
not double-count. Touches >10 files. Estimate: a 4-6 slice program
of its own.

**Risk.** **High.** Two issues to design around:

  - **Double-counting.** Admin API reports all usage; ccusage reports
    Claude-Code-only. Their intersection needs deterministic
    deduplication or the totals card lies.
  - **Auth surface.** Admin API keys are organization-level — losing
    one is worse than losing a user-level key. Storage / rotation
    is its own concern.

**Why this is parked, not punted.** The Daily view's v1 framed
agent detection as "missing" — it isn't missing, it's outside our
ingestion boundary. Surfacing a constant chip + caption is the
honest v1. This slice is the *real* fix; defer until the Daily
view's actual usage suggests the client-source axis is worth the
risk and effort budget.

## Suggested order

`18 → 19 → 20 → 21 → 22 → 23 → 24` — each independent,
value-per-effort drops at 21 and again at 22. Slices 23 (budget,
forward-looking) and 24 (forensics, backward-looking) are their
own category and can be picked up out of order. Slices 25 / 26 /
27 (sidebar Models default — three independent approaches to the
same pain) can land any time; start with 25 for the lowest-risk
first pass, escalate to 26 only if the shared-URL pain persists,
treat 27 as orthogonal (new-model auto-include is its own
concern). Stop at any rung.

Slice 28 (Admin API ingestion) is its own program of work — a
4–6 slice expansion, not a one-rung step. Pick it up only when
the Daily view's constant `Claude Code` chip starts costing real
analysis time. Until then the chip + caption is the honest v1.
