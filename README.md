# tokenscope-viz

Local-first interactive dashboard for [Claude Code](https://claude.com/claude-code)
token usage and cost. Built on top of [ccusage](https://www.npmjs.com/package/ccusage).

**Status:** Phase 1 scaffolding. See [PLAN.md](PLAN.md) for the full design.

## Quickstart (macOS)

```bash
# 1. Install runtimes (Homebrew only — no curl-pipe-to-shell installers)
brew install uv python@3.12 node

# 2. Clone and set up
git clone <this-repo>
cd tokenscope
./scripts/setup.sh

# 3. Run the dashboard
uv run streamlit run src/tokenscope/app.py
```

## Supply-chain policy

- All dependencies are pinned and lockfile-committed (`uv.lock`, `package-lock.json`).
- No `npx`, `pnpm dlx`, `yarn dlx`, `pipx run`, or `curl ... | sh` anywhere in
  this repo.
- ccusage is invoked as a strict-argv subprocess of the locally-installed
  `node_modules/.bin/ccusage`. Never via npx.

## Tests

```bash
uv run pytest                  # unit tests only (default)
uv run pytest -m integration   # opt-in: shells out to real ccusage
```

## License

MIT.
