#!/usr/bin/env bash
# Idempotent project setup. Assumes `brew install uv python@3.12 node` is done.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found. Run: brew install uv" >&2
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    echo "ERROR: npm not found. Run: brew install node" >&2
    exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
    echo "ERROR: brew not found. See https://brew.sh" >&2
    exit 1
fi

BREW_PY312="$(brew --prefix python@3.12 2>/dev/null || true)/bin/python3.12"
if [[ ! -x "${BREW_PY312}" ]]; then
    echo "ERROR: Python 3.12 not found at ${BREW_PY312}. Run: brew install python@3.12" >&2
    exit 1
fi

echo "==> uv venv (pinned to brew python@3.12 at ${BREW_PY312})"
uv venv --python "${BREW_PY312}"

echo "==> uv sync (installs pinned deps from uv.lock if present, otherwise resolves)"
if [[ -f uv.lock ]]; then
    uv sync --frozen --extra dev
else
    uv sync --extra dev
fi

echo "==> npm ci (installs ccusage from package-lock.json)"
if [[ -f package-lock.json ]]; then
    npm ci
else
    npm install
fi

echo "==> Setup complete. Next:"
echo "    uv run streamlit run src/tokenscope/app.py"
