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
Overview. Top-N (configurable, default 5) sessions ranked by cost,
each tagged with a one-line **reason** explaining what drove the spend:

- *Output-heavy* — when `output_tokens / total_tokens > ~25%`.
  Output tokens cost ~5× input rate, so an output-dominated session
  costs disproportionately for its size.
- *Cache-write heavy* — when `cache_create_tokens / total_tokens > ~20%`.
  Lots of fresh context prep without amortising it over many reads.
- *Low cache hit* — when the session's
  `cache_read / (input + cache_create + cache_read) < 50%`.
  Indicates context wasn't being reused.
- *Heavy model* — when only opus/sonnet was used for a high-token
  session that haiku could plausibly have handled. Soft signal.
- *Large volume* — fall-through when none of the above dominates;
  it just used a lot of tokens.

Each row in the table includes the session id (friendly-labelled),
cost, total tokens, the reason tag, and an "Open" button that drills
into the existing Session-detail view.

**Why.** Headline cost numbers tell you *what* — this view tells you
*why*. A user looking at "$300 this week" can see which three
sessions accounted for it, and whether the driver was a model choice
(switchable), a cache miss (fixable), or just sustained heavy use
(expected).

**Effort.** Medium. Needs:
- `analytics.session_cost_drivers(session)` returning a list of
  matching tag strings (a session can be both output-heavy AND
  low-cache, in which case both tags show).
- `analytics.expensive_sessions(session_report, n=5)` — pure-function
  ranker that returns annotated rows.
- UI table on Overview (Enterprise-only — the same gating logic the
  Cost-composition panel uses).
- Tuning the thresholds. They want to live in
  `tokenscope.config.toml` so the operator can dial them per-team:
  ```toml
  [forensics]
  output_heavy_threshold = 0.25
  cache_write_heavy_threshold = 0.20
  low_cache_threshold = 0.50
  top_n = 5
  ```

**Risk.** Medium. Heuristics can mislead — a user with one
genuinely-justified output-heavy session might roll their eyes at
the "Output-heavy" tag. Mitigations:
- Tags are *signals*, not verdicts. Show all that match, not just one.
- Add a caption disclosing the thresholds and that these are
  heuristics ("based on token mix; your context may make some of
  these expected").
- Skip the "Heavy model" tag in v1 — too judgemental to ship without
  per-task context.

## Suggested order

`18 → 19 → 20 → 21 → 22 → 23 → 24` — each independent,
value-per-effort drops at 21 and again at 22. Slices 23 (budget,
forward-looking) and 24 (forensics, backward-looking) are their own
category and can be picked up out of order. Stop at any rung.
