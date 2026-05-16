# tokenscope — visualisation plan (ccusage-backed)

**Date:** 2026-05-16
**Branch:** `claude/optimistic-morse-caaf76` (worktree)
**Status:** plan finalised; awaiting "go" before any code is written

---

## 1. What we're building

A local, interactive web dashboard that visualises Claude Code token usage and
cost, sourced entirely from [`ccusage`](https://www.npmjs.com/package/ccusage).
The dashboard itself is **Python (Streamlit + Plotly)**; ccusage is invoked as a
pinned subprocess (never via `npx`). User runs one command, a browser tab opens
with charts they can drill into.

**Why ccusage as the data layer:**

- It already parses every local Claude Code session log
- Already does pricing (it pulls from LiteLLM on each run — 2,707 models loaded in
  the sample run today)
- Already exposes structured JSON for every report (daily / weekly / monthly /
  session / blocks)
- Already handles cache-create vs cache-read accounting
- Already supports timezone, project filtering, breakdown by model

So we don't reimplement parsing or pricing. We wrap ccusage's JSON in an
interactive UI. This is a deliberate pivot away from the previous C#/OTEL/Grafana
stack on `main` — that build duplicated work ccusage already does well.

**Supply-chain hygiene (non-negotiable for this project).** Same rule across
every layer we touch:

- **Install tooling (macOS):** use **Homebrew** for `uv`, Python, Node,
  Docker, anything else. Never `curl ... | sh`, `wget ... | sh`, or any
  unpinned one-liner install script. Brew formulas are auditable, checksummed,
  and sandboxed under `/opt/homebrew`.
- **Node side (ccusage):** never `npx`, `pnpm dlx`, or `yarn dlx`. ccusage is
  pinned inside a small sibling `package.json` and resolved via committed
  `package-lock.json` + `npm ci`; the Python code shells out to the resolved
  `node_modules/.bin/ccusage`. No version drift, no on-demand registry fetch.
- **Python side (the dashboard):** never `pipx run <pkg>` of an unpinned
  package, never `uv run --with <pkg>` of an unpinned package, never
  `pip install` without a lockfile. We use **`uv`** for environment + lockfile
  (`uv.lock` committed, `uv sync --frozen` in any setup script). Hashes in
  the lockfile give integrity verification equivalent to npm's.
- **For end users:** install via `brew install` (if/when we publish a brew
  formula), or `uv tool install tokenscope-viz` from PyPI (`tokenscope` is
  already taken), or `git clone && uv sync --frozen && uv run tokenscope`.
  Never an unpinned `pipx run` or curl-pipe-to-shell recommendation in the
  README.

**Versions observed today:** ccusage `18.0.11` under Node `v26.0.0` (Node
itself installed via Homebrew). System Python is `3.9.6` (EOL October 2025 —
not usable), `uv` is not yet installed. Homebrew is already present at
`/opt/homebrew/bin/brew` (v5.1.11).

Phase 1 installs `uv` and Python 3.12 via Homebrew: `brew install uv
python@3.12`. Brew formulas are auditable Ruby files with SHA256-checksummed
downloads, installed into the sandboxed `/opt/homebrew` prefix — no
curl-pipe-to-shell, no auto-fetched install scripts. System Python 3.9.6 is
untouched. Exact Streamlit / Plotly / pandas versions captured in `uv.lock`
during phase 1.

**Note on the samples in §2 below:** I gathered them with `npx -y ccusage@latest`
before this constraint was on the table. Those JSON shapes are still accurate
(ccusage's output format is the same whichever way you launch it), but I won't
invoke it that way again — phase 1 will pin it as a dep and re-verify against
the locally-resolved binary.

---

## 2. What ccusage actually gives us (verified live)

Pulled today against the local Claude Code logs (see note in §1 about how —
won't be repeated; samples are still valid).

### 2.1 Top-level commands

```
ccusage daily       → grouped by date
ccusage weekly      → grouped by ISO week
ccusage monthly     → grouped by month
ccusage session     → grouped by conversation session
ccusage blocks      → grouped by 5-hour billing windows (incl. --active)
ccusage statusline  → compact line for Claude Code statusline hook
```

Useful flags: `--json`, `-s/--since YYYYMMDD`, `-u/--until YYYYMMDD`,
`-b/--breakdown` (per-model split), `-i/--instances` (per-project),
`-p/--project <name>`, `-z/--timezone`, `--offline` (cached pricing).

### 2.2 Daily JSON shape (real sample)

```json
{
  "daily": [
    {
      "date": "2026-04-01",
      "inputTokens": 964,
      "outputTokens": 160832,
      "cacheCreationTokens": 831622,
      "cacheReadTokens": 179919073,
      "totalTokens": 180912491,
      "totalCost": 99.0008208,
      "modelsUsed": ["claude-opus-4-6", "claude-haiku-4-5-20251001"],
      "modelBreakdowns": [
        {
          "modelName": "claude-opus-4-6",
          "inputTokens": 941,
          "outputTokens": 157819,
          "cacheCreationTokens": 811148,
          "cacheReadTokens": 179870945,
          "cost": 98.9553275
        },
        {
          "modelName": "claude-haiku-4-5-20251001",
          "inputTokens": 23,
          "outputTokens": 3013,
          "cacheCreationTokens": 20474,
          "cacheReadTokens": 48128,
          "cost": 0.0454933
        }
      ]
    }
  ]
}
```

### 2.3 Session JSON shape (real sample)

```json
{
  "sessions": [
    {
      "sessionId": "-Users-quintin-johnsmith-Documents-RiderProjects-tokenscope",
      "inputTokens": 949,
      "outputTokens": 685485,
      "cacheCreationTokens": 993585,
      "cacheReadTokens": 393953287,
      "totalTokens": 395633306,
      "totalCost": 220.32841975,
      "lastActivity": "2026-05-14",
      "modelsUsed": ["claude-opus-4-7"],
      "modelBreakdowns": [ ... ],
      "projectPath": "Unknown Project"
    }
  ]
}
```

Note: `sessionId` is a slug-encoded absolute path to the project directory — we
can derive a project name from it for grouping.

### 2.4 Blocks JSON shape (real sample, includes active block)

```json
{
  "blocks": [
    {
      "id": "2026-05-16T13:00:00.000Z",
      "startTime": "2026-05-16T13:00:00.000Z",
      "endTime": "2026-05-16T18:00:00.000Z",
      "actualEndTime": "2026-05-16T13:41:04.632Z",
      "isActive": true,
      "isGap": false,
      "entries": 5,
      "tokenCounts": {
        "inputTokens": 10,
        "outputTokens": 6272,
        "cacheCreationInputTokens": 28861,
        "cacheReadInputTokens": 179884
      },
      "totalTokens": 215027,
      "costUSD": 0.42717325,
      "models": ["claude-opus-4-7"],
      "burnRate": {
        "tokensPerMinute": 74047.24,
        "tokensPerMinuteForIndicator": 2163.29,
        "costPerHour": 8.83
      },
      "projection": {
        "totalTokens": 19371597,
        "totalCost": 38.48,
        "remainingMinutes": 259
      }
    }
  ]
}
```

Blocks are gold for live monitoring: each block already carries `burnRate` and a
projected end-of-window cost.

### 2.5 What the default table view looks like

```
┌──────────┬───────────────────┬──────┬─────────┬───────────┬────────────┬────────────┬─────────┐
│ Date     │ Models            │ Input│ Output  │ Cache Cr. │ Cache Read │ Total      │ Cost    │
├──────────┼───────────────────┼──────┼─────────┼───────────┼────────────┼────────────┼─────────┤
│ 26-05-14 │ - opus-4-7        │  189 │ 121,354 │   167,572 │132,049,118 │132,338,233 │  $70.11 │
│ 26-05-13 │ - opus-4-7        │  760 │ 564,131 │   826,013 │261,904,141 │263,295,045 │ $150.22 │
│ 26-05-12 │ - opus-4-7        │  377 │ 192,902 │   494,216 │ 55,925,978 │ 56,613,473 │  $35.88 │
└──────────┴───────────────────┴──────┴─────────┴───────────┴────────────┴────────────┴─────────┘
```

Useful for sanity-checking, not for spotting trends. That's the gap we fill.

---

## 3. The visualisation

### 3.1 Information architecture — drill paths

Two entry points, both drill the same way:

```
Monthly overview ──► Daily breakdown ──► Sessions on that day ──► Blocks within session
                                    └──► Model breakdown for that day
Active block (live)  ──► current burn rate, projection, model split
Project view (-i)    ──► per-project cost, drill into its sessions
```

Every chart element clickable. Crumbs at the top so you can climb back up.
Global filter bar (date range, model, project) persists across drills.

### 3.2 Chart inventory

| View | Chart type | What it shows | Drill target |
|---|---|---|---|
| Overview | Stacked area | Daily cost, stacked by model | click day → Day detail |
| Overview | Line | 7-day rolling cost average | click point → Day detail |
| Overview | KPI cards | MTD cost, today cost, active block burn $/hr, cache hit ratio | — |
| Overview | Stacked bar | Daily token mix (input / output / cache-create / cache-read) | click day → Day detail |
| Day detail | Donut | Cost share by model for the day | click slice → filter by model |
| Day detail | Table | Sessions on that day | click row → Session detail |
| Session | Timeline | Blocks within the session along time | click block → Block detail |
| Session | Stacked bar | Token mix for the session | — |
| Block | Gauge + projection | Burn rate, projected end-of-window cost | — |
| Cache | Ratio gauge | `cacheRead / (input + cacheCreate + cacheRead)` over time | — |
| Cache | Bar | Estimated $ saved by cache reads vs uncached input price | — |
| Projects | Horizontal bar | Top projects by cost (`-i` flag) | click bar → project sessions |
| Models | Sankey | input/output/cache-create/cache-read → models → cost | hover for $/token |

### 3.3 Drill-down mechanics (Streamlit-specific)

Four sidebar controls; the first three filter data, the fourth changes only
how cost is labelled:

1. **Date range picker** (default: last 30 days). Triggers a re-shell of ccusage
   with `-s`/`-u`, results cached via `@st.cache_data(ttl=30)`.
2. **Model filter** (multi-select, populated from `modelsUsed` across the range).
3. **Project filter** (uses `-p` or post-filters on slugified `sessionId`).
4. **Plan selector** (Enterprise / Pro / Max 5× / Max 20×). Pure labelling —
   does not alter any token counts, cache stats, or cost numbers. When
   anything other than Enterprise is selected, an info banner sits above the
   cost KPIs: *"Showing API-equivalent cost — your plan is flat-rate at
   $N/month."* Default: Enterprise (no banner).

State strategy:

- **Drill state** (current view, selected day / session / block) lives in
  `st.session_state` and is mirrored into `st.query_params` so the URL is
  shareable and the back button works. Available since Streamlit 1.30.
- **Click-to-drill on charts** uses Plotly's `on_select` event via
  `st.plotly_chart(..., on_select="rerun")` — gives us "click this day in the
  stacked-area chart" → drill into the day view, without a custom component.
- **Partial reruns** via `@st.fragment` on each chart, so changing one filter
  doesn't redraw the whole page.

If the rerun-on-interaction model proves limiting for the nested drill paths in
§3.1, the fallback inside Python is **Plotly Dash** (more SPA-like, more
boilerplate). Cross that bridge if we hit it.

---

## 4. Language analysis

The user asked us to evaluate options and justify the choice. Five candidates,
ranked.

### 4.1 TypeScript / Node — close runner-up, not chosen

**Pros**
- ccusage is npm-native. Same runtime, no language boundary. We pin it as a
  `dependencies` entry (`npm install ccusage`) so it's version-locked and
  integrity-checked via `package-lock.json`. Preference: import its programmatic
  API if one is exported (to be verified in phase 1 — *I won't assert it exists
  without checking the package's published exports*); fallback is to spawn the
  locally-resolved `node_modules/.bin/ccusage` directly. Either way, no `npx`.
- Best-in-class browser charting: **Apache ECharts** (rich, interactive,
  drill-friendly), **Recharts** (React-idiomatic), **Visx**, **Plotly.js**, **D3**.
  Every chart in §3.2 is a few lines.
- TypeScript types make ccusage's JSON shape self-documenting in the IDE.
- Cross-platform by default (Node runs identically on macOS and Windows — the
  CLAUDE-level requirement on the old project).

**Cons**
- Yet another front-end build to maintain (Vite/React/etc.).
- Charting libraries churn faster than backend code.

**Verdict:** Strongest single-stack story on paper. Lost the head-to-head
because the user decided Python — see §4.2 and §4.6. The TS rationale stays
in this document so the decision trail is auditable.

### 4.2 Python (Streamlit + Plotly) — **SELECTED**

**Pros**
- Streamlit is the fastest path from JSON to interactive dashboard. ~200–400
  lines for everything in §3.2.
- pandas makes the group/pivot/rolling-average work trivial — and meaningful
  when we add anything beyond what ccusage already computes (anomaly
  detection on burn rate, MTD forecasting, per-project trend lines).
- Plotly figures are interactive (zoom, hover, click) out of the box, and
  Streamlit's `on_select="rerun"` mode gives us click-to-drill without a
  custom component.
- `st.query_params` + `st.session_state` give URL-shareable drill state.
- `uv` is excellent — fast, lockfile-driven, gives parity with npm's
  integrity story.
- User chose this language (explicit decision, 2026-05-16).

**Cons (eyes-open)**
- Adds Python to the runtime requirements on top of Node (ccusage is Node).
  Mitigated: most Claude Code users already have Node installed (Claude Code
  itself is a Node CLI).
- Streamlit's rerun-on-interaction model is not as snappy as a true SPA.
  Mitigated by `@st.fragment` for partial reruns (Streamlit 1.33+). If nested
  drills become unwieldy, the in-Python fallback is Plotly Dash.
- Distribution is heavier than a Node `npm install -g`. Default path is
  `uv tool install tokenscope` once on PyPI, or `uv sync && uv run tokenscope`
  from a clone.

**Verdict:** This is the v1 stack.

### 4.3 C# / .NET (Blazor or ASP.NET + Chart.js)

**Pros**
- What the previous build used; you (Quintin) work in .NET daily.
- Blazor Server is genuinely fine for a local dashboard.

**Cons**
- We'd shell out to a Node tool from C#, parse its JSON into records, and then
  render — three languages of boilerplate where TypeScript needs one.
- Charting in Blazor leans on JS-interop wrappers around Chart.js / ECharts
  anyway. No win.
- Heavier runtime to ship than Node.
- The old tokenscope on `main` already did this and the conclusion was that
  ccusage made it redundant. Repeating the language choice repeats the trap.

**Verdict:** Reject. No compelling advantage over TS, and active drawbacks.

### 4.4 Java (Spring Boot + Vaadin / JS frontend)

**Pros**
- None specific to this problem.

**Cons**
- Heaviest runtime of the candidates.
- Weakest charting story without dropping into JS anyway.
- Same shell-out-to-Node problem as C# but with more ceremony.

**Verdict:** Reject.

### 4.5 Go (single binary + embedded web UI)

**Pros**
- Beautiful distribution — one static binary, no runtime.
- `os/exec` to ccusage's locally-installed binary is trivial.

**Cons**
- The UI still has to be HTML/JS, so we end up writing TS anyway — Go just
  becomes a thin wrapper.
- ccusage is a Node package; either we still require Node on the user's
  machine to run it, or we re-implement the parsing ourselves, which throws
  away the whole reason for picking ccusage as the data layer.

**Verdict:** Reject for v1. Reconsider if we ever want a zero-dependency
ccusage-free fork.

### 4.6 Final stack (Python / Streamlit)

- **Dashboard:** **Streamlit** (latest stable, pinned), **Plotly** for charts,
  **pandas** for shaping. One process, no separate frontend build, no
  React/Vite/Tailwind to maintain.
- **ccusage bridge:** a small sibling `package.json` in the repo pins ccusage
  exactly; `package-lock.json` committed; setup runs `npm ci`. Python uses
  `subprocess.run(["node_modules/.bin/ccusage", ...])` with strict
  argument-list invocation (no shell interpolation, never `shell=True`).
  Caching via `@st.cache_data(ttl=30)` so the same query in the same minute
  doesn't re-shell.
- **Environment:** **`uv`** for everything — `pyproject.toml` + `uv.lock`
  committed, `uv sync --frozen` is the canonical install. Targets
  Python 3.12+ (Streamlit + modern type hints).
- **Distribution:**
  - **Personal use today:** `git clone && uv sync --frozen && uv run tokenscope`.
  - **Published path (later):** `uv tool install tokenscope-viz` from PyPI
    (`tokenscope` is taken; PyPI distribution name is `tokenscope-viz`, the
    installed console script remains `tokenscope`). Same discipline — no
    `pipx run`, no `uv run --with` of unpinned packages.
  - **Reproducible path:** Dockerfile (Python + Node base image, both pinned)
    for users who want zero local installs.
- **Lockfile policy:** `uv.lock` and `package-lock.json` both committed; CI
  uses `uv sync --frozen` and `npm ci`; dep bumps are deliberate PRs.

End user prereq: Node (for ccusage to run under). Acceptable because Claude
Code already requires Node, so the audience has it.

---

## 5. Architecture (one paragraph)

`uv run tokenscope` invokes `streamlit run src/tokenscope/app.py`, which boots
Streamlit on `127.0.0.1:<port>` and opens the browser. The app's data layer is
a thin `ccusage.py` module that wraps `subprocess.run(["node_modules/.bin/ccusage",
<command>, "--json", *flags])` with typed return models (pydantic) matching
the JSON shapes captured in §2. All ccusage calls go through
`@st.cache_data(ttl=30)` so the same query in the same window doesn't re-shell.
The sidebar holds the date / model / project filters; the main pane holds the
chart grid; clicks on Plotly charts fire `on_select="rerun"` events that mutate
`st.session_state` and `st.query_params`, which the next render reads to decide
the current view. No telemetry. No remote calls from our code; ccusage's own
LiteLLM pricing fetch can be disabled via its `--offline` flag, exposed as a
sidebar toggle. Works identically on macOS and Windows (Python + Streamlit are
cross-platform; the only OS-touching code is the path to ccusage's binary,
which `pathlib` handles).

---

## 6. Proposed phases

Same "stop at every boundary" rhythm as the previous project.

**Phase 0 — this plan.** Approval before any code.

**Phase 1 — scaffolding + ccusage bridge.**
- `brew install uv python@3.12` — both tools installed via Homebrew into
  `/opt/homebrew` (auditable formulas, no curl-pipe-to-shell). System
  Python 3.9.6 untouched. Node is already brew-installed.
- `uv venv --python "$(brew --prefix python@3.12)/bin/python3.12"` — venv
  pinned explicitly to the brew Python so `uv` doesn't silently download
  its own python-build-standalone.
- `pyproject.toml` with Streamlit, Plotly, pandas, pydantic pinned via `uv`;
  `uv.lock` committed. Project name is `tokenscope-viz`, console script
  entry point is `tokenscope`.
- Sibling `package.json` pinning `ccusage`; `package-lock.json` committed.
- Setup script: `uv sync --frozen && npm ci`. No `npx`, no unpinned `pipx`/`uv`
  invocations anywhere.
- `src/tokenscope/ccusage.py` — typed subprocess wrapper around
  `node_modules/.bin/ccusage` for each of the five commands (daily / weekly /
  monthly / session / blocks). Strict arg-list invocation, no `shell=True`.
- Pydantic models matching §2 JSON shapes; unit tests against captured
  fixtures so we don't have to re-shell ccusage in CI.
- `src/tokenscope/app.py` — empty Streamlit shell that boots and prints
  the ccusage version it resolved.
- Smoke test on macOS (Windows deferred per existing project memory).

**Phase 2 — data layer.** Promote the ccusage wrapper to a real cache layer
(`@st.cache_data(ttl=30)`), add date-range plumbing (`-s`/`-u`), add the
project/instances flag handling, normalise model names. Pure-function helpers
in `analytics.py` for the rollups the dashboard needs (rolling averages, cache
hit ratio, $ saved).

**Phase 3 — overview view.** Sidebar with date range, offline toggle, and
**Plan selector** (Enterprise / Pro / Max 5× / Max 20×) wired to the
API-equivalent-cost banner. Main pane: KPI cards (MTD cost, today, active-block
burn $/hr, cache hit ratio), daily stacked-area cost by model, 7-day rolling
line, daily token-mix stacked bar. No drill-down yet.

**Phase 4 — drill-down.** Day detail, session detail, block detail. URL-state
via `st.query_params`. Click-to-drill on Plotly charts via
`on_select="rerun"`. Model + project filters added to sidebar. Breadcrumbs.

**Phase 5 — cache + models views.** Cache hit-ratio gauge over time, estimated
$ saved vs uncached input pricing, Sankey of token flow (Plotly Sankey).

**Phase 6 — active block live view.** Auto-refresh `--active` block every 30s
(`st.fragment(run_every="30s")`), burn gauge + projection.

**Phase 7 — packaging.** Console script entry point (`tokenscope`),
`README.md` with screenshots, optional Dockerfile, dry-run of PyPI publish.

Each phase ends with a working build, a commit, and a stop-for-go.

---

## 7. Decisions (all resolved 2026-05-16)

1. ~~**TypeScript vs Python.**~~ **Decided: Python.**
2. ~~**Streamlit vs Dash.**~~ **Decided: Streamlit.** Plotly figures port to
   Dash if we ever need to migrate.
3. ~~**Subscription-cost framing.**~~ **Decided: keep plan framing** —
   sidebar Plan selector (Enterprise / Pro / Max 5× / Max 20×) with an
   "API-equivalent cost" banner above cost KPIs for flat-rate plans. Pure
   labelling; no separate cost math. See §3.3.
4. ~~**Naming.**~~ **Decided: `tokenscope-viz` on PyPI** (`tokenscope` is
   taken). GitHub repo stays `tokenscope`. Installed console script stays
   `tokenscope`.
5. ~~**Python version floor.**~~ **Decided: 3.12, installed via `uv python
   install 3.12`.** System Python 3.9.6 is EOL and unused.

---

## 8. What is NOT in scope for v1

- Any rewrite of ccusage's parsing or pricing logic
- OpenTelemetry, Prometheus, Grafana (deleted with the previous build)
- Remote / multi-host aggregation
- Auth, multi-user, cloud hosting
- Writing to Claude logs or any side-effecting action on the user's session data
