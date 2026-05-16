# tokenscope-viz

Local-first interactive dashboard for [Claude Code](https://claude.com/claude-code)
token usage and cost, built on top of [ccusage](https://www.npmjs.com/package/ccusage).
The dashboard is Python ([Streamlit](https://streamlit.io) + [Plotly](https://plotly.com/python/));
ccusage is invoked as a strict-argv subprocess of the locally-installed,
lockfile-pinned binary. One process, one browser tab, your data never leaves
the machine.

## At a glance

Six views, all driven from one sidebar:

- **Overview** — KPI cards (window cost, last day, active-block burn $/hr,
  cache hit ratio) plus three clickable Plotly charts (stacked-area by
  model family, 7-day rolling line, daily token mix). Click any point to
  drill into that day.
- **Live** — burn-rate gauge + projection for the currently-active 5-hour
  billing block. Auto-refreshes every 30s via `st.fragment(run_every=30)`.
- **Cache** — window-aggregate cache hit-ratio KPI, per-day hit-ratio
  line, and a stacked bar of estimated dollars saved by cache reads vs.
  uncached input pricing.
- **Models** — Plotly Sankey of token-kind → model family, with each
  family's aggregate cost baked into its node label.
- **Day / Session / Block detail** — reached by clicking through from
  Overview. Breadcrumbs trace back; URL state is mirrored into
  `st.query_params` so views are shareable.

Sidebar controls are shared across every view: date range (default last
30 days), `--offline` toggle, project filter (auto-populated from
ccusage's `--instances` keys), model multi-select (auto-populated and
applied post-fetch), and a plan selector with an API-equivalent-cost
banner for flat-rate plans.

## Quickstart (macOS)

```bash
# 1. Runtimes — Homebrew only. No curl-pipe-to-shell installers.
brew install uv python@3.12 node

# 2. Clone + install pinned deps.
git clone https://github.com/Quintin-John/tokenscope.git
cd tokenscope
./scripts/setup.sh            # uv sync --frozen + npm ci

# 3. Run the dashboard.
uv run tokenscope             # console script — re-execs streamlit bound
                              # to 127.0.0.1, opens a browser tab.
```

The console script accepts any flag `streamlit run` accepts. To pin the
port:

```bash
uv run tokenscope --server.port=8501
```

The bundled `.streamlit/config.toml` hides the deploy button and disables
telemetry — this is a local-only tool.

## Configuration

Sidebar controls cover the day-to-day knobs. A few longer-lived defaults
live in source so prices and prefs are auditable and lockfile-tracked:

- **`src/tokenscope/pricing.py`** — input-token pricing per model family
  in USD per million tokens. Drives the "estimated $ saved" bar on the
  Cache view. Update one line when Anthropic prices move.
- **`src/tokenscope/plans.py`** — flat-rate-plan metadata for the
  sidebar Plan selector (Enterprise / Pro / Max 5× / Max 20×). Pure
  labelling per PLAN.md §3.3; selecting a non-Enterprise plan only adds
  a banner explaining the displayed cost is API-equivalent.
- **`.streamlit/config.toml`** — `client.toolbarMode = "viewer"` (hides
  Deploy), `browser.gatherUsageStats = false`.

## Architecture

```
src/tokenscope/
├── ccusage.py        # subprocess wrapper around node_modules/.bin/ccusage
│                     # strict argv, never shell=True, never npx
├── models.py         # pydantic models for ccusage --json output
│                     # extra="forbid" everywhere except DailyEntry.project
├── query.py          # frozen-dataclass Query — hashable, --key=value form
├── data.py           # @st.cache_data(ttl=30) wrappers over ccusage.*
├── pricing.py        # model-family → $/MTok lookup
├── plans.py          # subscription plan metadata
├── analytics.py      # pure-function rollups (100% test coverage)
├── navigation.py     # URL state (view, day, session, block) round-trips
│                     # to/from st.query_params
├── app.py            # entry point + top-of-page page selector
└── ui/
    ├── sidebar.py    # date range, offline, project, models, plan
    ├── overview.py   # KPIs + 3 charts with on_select="rerun" drill
    ├── live.py       # @st.fragment(run_every=30) burn gauge
    ├── cache.py      # hit-ratio + dollars-saved
    ├── models.py     # Sankey
    ├── day.py        # day-detail with sessions + blocks lists
    ├── session.py    # session-detail
    ├── block.py      # block-detail
    ├── breadcrumbs.py # tertiary-button trail for drill views
    └── charts.py     # all Plotly figure builders (no Streamlit dep)
```

`ccusage.py` knows nothing about Streamlit; `data.py` is the only module
that imports both. `analytics.py` and `ui/charts.py` are pure — they take
pydantic models and return primitives / Plotly figures, no I/O, no
caching. UI modules touch `st.*`. This split is what keeps the test
suite fast and Streamlit-free.

## Development

```bash
./scripts/setup.sh                 # idempotent — uv sync --frozen + npm ci
uv run pytest                      # unit + parse tests (default; ~150 tests, <1s)
uv run pytest -m integration       # opt-in: shells out to real ccusage
```

Coverage targets: `analytics.py`, `navigation.py`, and `pricing.py` all
hold **100%** line coverage. The chart builders return `plotly.Figure`
objects so they can be inspected in tests without a Streamlit runtime;
the UI modules are smoke-tested via `streamlit.testing.v1.AppTest`.

Phase boundaries are described in [PLAN.md §6](PLAN.md#6-proposed-phases).
Each phase ends with a working build and a stop-for-go.

## Docker (optional)

A pinned multi-stage `Dockerfile` is provided for users who want zero
local installs. Node 20.19.4 is copied straight from the official
`node:20.19.4-bookworm-slim` image (so no third-party setup script
runs); Python 3.12 comes from `python:3.12-slim-bookworm`. uv and
ccusage are installed at lockfile-frozen versions.

```bash
docker build -t tokenscope .
docker run --rm -p 8501:8501 \
    -v "$HOME/.config/claude":/root/.config/claude:ro \
    tokenscope
# open http://127.0.0.1:8501
```

The volume mount exposes your Claude Code logs read-only into the
container so ccusage can read them; without it the dashboard will load
with an empty data set.

## PyPI publish path

`tokenscope-viz` is the PyPI distribution name (`tokenscope` was taken).
The installed console script stays `tokenscope`.

Build artefacts locally with the standard `uv build`:

```bash
uv build                          # writes wheel + sdist under dist/
python -m zipfile -l dist/*.whl   # inspect the wheel manifest
```

When ready to publish (not yet — this is a dry-run release), `uv
publish` handles upload with a pre-set token. The repository does not
ship CI workflows (per the project's no-`.github/workflows/` policy);
releases are cut manually from a clean tagged tree.

## Supply-chain policy

- Every dependency is pinned and lockfile-committed (`uv.lock`,
  `package-lock.json`). Setup uses `uv sync --frozen` and `npm ci`.
- No `npx`, `pnpm dlx`, `yarn dlx`, `pipx run`, or unpinned `uvx` calls
  anywhere — in code, scripts, README, or docs.
- ccusage is a runtime dep (`package.json`) and invoked as
  `subprocess.run([node_modules/.bin/ccusage, ...])` with strict argv.
  Never `shell=True`. String args are passed in the `--key=value` form so
  values starting with `-` (ccusage's slug-encoded project ids) can't be
  re-parsed as flags.
- Pydantic models use `extra="forbid"` so unannounced ccusage schema
  drift trips a loud test failure rather than silent data loss.

## Not in scope (v1)

Per [PLAN.md §8](PLAN.md#8-what-is-not-in-scope-for-v1):

- Re-implementing ccusage's parsing or pricing logic.
- OpenTelemetry / Prometheus / Grafana.
- Remote or multi-host aggregation.
- Auth, multi-user, cloud hosting.
- Any write back to Claude Code's session data.

## License

MIT. See `pyproject.toml`.
