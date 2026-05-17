# tokenscope-viz

Local-first interactive dashboard for [Claude Code](https://claude.com/claude-code)
token usage and cost, built on top of [ccusage](https://www.npmjs.com/package/ccusage).
The dashboard is Python ([Streamlit](https://streamlit.io) + [Plotly](https://plotly.com/python/));
ccusage is invoked as a strict-argv subprocess of the locally-installed,
lockfile-pinned binary. One process, one browser tab, your data never leaves
the machine.

## At a glance

Seven views, all driven from one sidebar:

- **Overview** — KPI cards (Window cost / Last day / Active-block $/hr /
  Cache hit ratio) plus three clickable Plotly charts (stacked-area by
  family, 7-day rolling line, daily token mix). Click any point to
  drill into that day. On Enterprise, an expandable **Cost composition**
  panel breaks the bill down by token kind (input / output /
  cache_create / cache_read) using live LiteLLM rates.
- **Live** — burn-rate gauge + projection for the currently-active
  5-hour billing block. Auto-refreshes every 30s.
- **Cache** — cache hit-ratio KPI, effective $/MTok (blended actual
  rate), and per-day hit-ratio line.
- **Models** — per-model breakdown table, cost-share donut, and a
  Sankey of token-kind → model family. Token / cost width toggle and
  Top-N family collapse. Per-family drill buttons route to a
  pre-filtered Overview.
- **Day / Session / Block detail** — drill-down views reached by
  clicking charts or table rows. Session detail includes the
  blocks-on-this-day timeline. Breadcrumbs trace back; URL state is
  mirrored into `st.query_params` so views are shareable.

Sidebar (shared across every view): date range, `--offline` toggle,
project filter (auto-populated, friendly labels), model multi-select
(short labels), plan selector (Enterprise / Pro / Max 5× / Max 20×).
Timezone is auto-detected from the OS; `Reset filters` clears
everything back to defaults.

## Quickstart

Most users want Docker — zero local installs, the same image developers
use to ship.

### Docker (recommended for users)

```bash
git clone https://github.com/Quintin-John/tokenscope.git
cd tokenscope
docker build -t tokenscope .
docker run --rm -p 8501:8501 \
    -e TZ="$(readlink /etc/localtime | sed -E 's|.*/zoneinfo/||')" \
    -v "$HOME/.claude":/root/.claude:ro \
    tokenscope
# open http://127.0.0.1:8501
```

The volume mount exposes Claude Code's session history read-only into
the container so ccusage can read it; without it the dashboard loads
with an empty data set. On macOS the path is `~/.claude`.

The `-e TZ=...` line passes your host timezone into the container.
**Skip it and your costs will be wrong.** The container defaults to
`Etc/UTC`, which makes ccusage bucket day boundaries by UTC. For a
US-east user that pushes late-evening sessions onto the *next* UTC
date — so a 12-hour project shows up split across two days, and
sessions past 8pm on the window's `--until` date silently disappear
because they belong to "tomorrow" in UTC. On a 6-week window we saw
$206 (~9%) go missing this way. The shell expression resolves your
host's IANA zone (`America/New_York`, `Europe/London`, etc.) by
inspecting `/etc/localtime`; if it doesn't work on your system, pass
the zone explicitly: `-e TZ=America/New_York`. The dashboard's
sidebar caption shows which zone got detected — if it says
`Etc/UTC`, the flag didn't take.

#### Diagnostic logging

Logs are **on by default** at `INFO` and go to stdout, so
`docker logs <container>` (and your terminal when running locally)
surfaces every ccusage invocation, every chart build, the
detected timezone at startup, and every user-driven navigation —
no env-var flip required.

Levels: `DEBUG` (adds verbose detail — full subprocess argv,
per-view render events, internal bucketing), `INFO` (default —
data-boundary calls + chart builds + nav), `WARNING` (silent
fallbacks: unknown model id, invalid TZ, pricing fetch failure,
phantom-trace scrubber), `ERROR` (subprocess failures, schema
parse failures).

Quieten it with `TOKENSCOPE_LOG_LEVEL=ERROR`; add detail with
`TOKENSCOPE_LOG_LEVEL=DEBUG`. The Dockerfile sets
`PYTHONUNBUFFERED=1` so lines flush immediately.

The format auto-detects: JSON one-record-per-line when stdout
isn't a TTY (Docker, pipes), human-readable single-line when it
is (your terminal). Override with `TOKENSCOPE_LOG_FORMAT=json`
or `=human`.

```bash
docker run --rm -p 8501:8501 \
    -e TZ="..." \
    -v "$HOME/.claude":/root/.claude:ro \
    tokenscope
# Logs are already streaming. Optional: add DEBUG detail or
# pipe through jq.
docker logs -f <container-id>
docker logs <container-id> | jq 'select(.message | startswith("ccusage."))'
```

Event-name convention: `domain.event[.detail]` (`ccusage.ok`,
`chart.token_mix.built`, `nav.route`, `tz.detected`, etc.) — grep
the stream by domain prefix to focus on a layer.

The Dockerfile is multi-stage and pinned: Node 20.19.4 from the
official `node:20-bookworm-slim` image (no third-party setup script
runs), Python 3.12.7 from `python:3.12-slim-bookworm`, `uv` and
ccusage at their lockfile versions. Verified end-to-end on macOS arm64
against Docker Desktop 29.4.3 — image ~880 MB, ready in a few seconds.

### Source install (for development)

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
port: `uv run tokenscope --server.port=8501`.

## Configuration

Day-to-day knobs are sidebar controls. Longer-lived defaults live in
files so they're auditable and source-controlled:

- **`tokenscope.config.toml`** (project root) — operator-tunable
  values: default date range, data-cache TTL, live-refresh cadence,
  the LiteLLM pricing URL + cache settings. The defaults match the
  baked-in fallbacks in `src/tokenscope/config.py`, so the file is
  optional; delete keys you don't want to override.
- **`src/tokenscope/plans.py`** — flat-rate plan metadata for the
  sidebar Plan selector (Enterprise / Pro / Max 5× / Max 20×).
  Selecting Pro / Max shows the flat fee as the Window-cost headline
  with "would cost $X at API rates" as the savings delta.
- **`.streamlit/config.toml`** — Streamlit chrome (hides Deploy button,
  disables telemetry, strips Ask-AI buttons from error popups).
- **Anthropic rates** are fetched live from
  [LiteLLM's public pricing JSON](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json)
  (the same source ccusage uses internally), cached under
  `~/.cache/tokenscope/` for a week. No hardcoded rates anywhere.

Override the timezone with the standard `TZ` env var if auto-detection
picks the wrong one.

## Architecture

```
src/tokenscope/
├── ccusage.py        # subprocess wrapper around node_modules/.bin/ccusage
│                     # strict argv, never shell=True, never npx
├── models.py         # pydantic models for ccusage --json output
│                     # extra="forbid" everywhere except DailyEntry.project
│                     # and BlocksReport.message (empty-range shapes)
├── query.py          # frozen-dataclass Query — hashable, --key=value form
├── data.py           # @st.cache_data wrappers caching raw JSON (not models)
├── pricing.py        # LiteLLM fetch + cache; per-model and per-family rates
├── plans.py          # subscription plan metadata
├── tz.py             # auto-detect IANA timezone + UTC↔local helpers
├── config.py         # tokenscope.config.toml loader with fallback defaults
├── analytics.py      # pure-function rollups (100% test coverage)
├── navigation.py     # URL state (view, day, session, block) round-trips
│                     # to/from st.query_params (100% test coverage)
├── app.py            # entry point + top-of-page page selector + CSS
└── ui/
    ├── _nav.py       # shared route_to() / handle_chart_drill() helpers
    ├── _data.py      # shared fetch+filter helper (load_daily)
    ├── sidebar.py    # date range, offline, project, models, plan + URL sync
    ├── overview.py   # KPIs + 3 charts + Cost-composition expander
    ├── live.py       # @st.fragment(run_every=30) burn gauge + typical band
    ├── cache.py      # hit-ratio + effective $/MTok
    ├── models.py     # per-model table, Sankey, donut, family drill
    ├── day.py        # day-detail with sessions + blocks lists
    ├── session.py    # session-detail + blocks-on-this-day timeline
    ├── block.py      # block-detail with burn gauge
    ├── breadcrumbs.py # trail across drill views
    └── charts.py     # all Plotly figure builders (no Streamlit dep)
```

Layering rule: `ccusage.py` knows nothing about Streamlit; `data.py` is
the only module that imports both. `analytics.py` and `ui/charts.py`
are pure — they take pydantic models and return primitives / Plotly
figures, no I/O, no caching. UI modules touch `st.*`. `ui/_nav.py` and
`ui/_data.py` are private shared glue. This split keeps the test suite
fast and Streamlit-free.

## Development

```bash
./scripts/setup.sh                 # idempotent — uv sync --frozen + npm ci
uv run pytest                      # full suite (~227 tests, ~2s)
uv run pytest -m integration       # opt-in: shells out to real ccusage
uv run pytest --cov=tokenscope     # full coverage report (~87% total)
```

Coverage targets: `analytics.py`, `models.py`, `navigation.py`,
`plans.py`, `query.py`, `__init__.py` all hold **100%** line coverage.
Total project coverage sits at 87% — UI modules covered via
`streamlit.testing.v1.AppTest` against a `mock_ccusage` fixture so unit
tests don't shell out.

Phase boundaries are described in [PLAN.md §6](PLAN.md#6-proposed-phases).
Every PLAN.md §3.1 drill path is implemented as of slice 17.

## PyPI publish path

`tokenscope-viz` is the PyPI distribution name (`tokenscope` was taken).
The installed console script stays `tokenscope`.

```bash
uv build                          # writes wheel + sdist under dist/
python -m zipfile -l dist/*.whl   # inspect the wheel manifest
```

When ready to publish, `uv publish` handles upload with a pre-set
token. The repository does not ship CI workflows (per the project's
no-`.github/workflows/` policy); releases are cut manually from a
clean tagged tree.

## Supply-chain policy

- Every dependency is pinned and lockfile-committed (`uv.lock`,
  `package-lock.json`). Setup uses `uv sync --frozen` and `npm ci`.
- No `npx`, `pnpm dlx`, `yarn dlx`, `pipx run`, or unpinned `uvx` calls
  anywhere — in code, scripts, README, or docs.
- ccusage is a runtime dep (`package.json`) and invoked as
  `subprocess.run([node_modules/.bin/ccusage, ...])` with strict argv.
  Never `shell=True`. String args use `--key=value` form so values
  starting with `-` (ccusage's slug-encoded project ids) can't be
  re-parsed as flags.
- Pydantic models use `extra="forbid"` so unannounced ccusage schema
  drift trips a loud test failure rather than silent data loss.
- LiteLLM pricing is fetched over HTTPS with cert verification (stdlib
  `urllib.request`); no third-party HTTP client added.

## Not in scope (v1)

Per [PLAN.md §8](PLAN.md#8-what-is-not-in-scope-for-v1):

- Re-implementing ccusage's parsing or pricing logic.
- OpenTelemetry / Prometheus / Grafana.
- Remote or multi-host aggregation.
- Auth, multi-user, cloud hosting.
- Any write back to Claude Code's session data.

## License

MIT. See `pyproject.toml`.
