# Reproducible Dockerfile for tokenscope-viz.
#
# Constraints (see README.md "Supply-chain policy"):
#  - No `curl ... | sh` install scripts (no NodeSource setup, no astral.sh
#    uv installer). Node comes straight from the official `node` image;
#    uv is pip-installed inside our pinned Python image.
#  - All deps frozen by lockfile: `uv sync --frozen` and `npm ci`.
#  - No `npx` anywhere.
#
# Built and verified on macOS arm64 against Docker Desktop / colima.
# Windows verification is deferred (project memory).

# ---- Stage 1: pull Node 20 from the official Debian-based image ----
FROM node:20.19.4-bookworm-slim AS node-source

# ---- Stage 2: builder — install Python + JS deps with the lockfiles ----
FROM python:3.12.7-slim-bookworm AS builder

# Bring Node + npm over from the official image. node:20-bookworm-slim
# installs Node into /usr/local, so the bin/lib paths line up cleanly
# with the python:3.12-slim-bookworm filesystem layout.
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Pinned uv version. Bump deliberately when you bump the host's brew uv.
RUN pip install --no-cache-dir uv==0.5.4

WORKDIR /app

# Bring lockfiles + manifests in first so the install layers cache on
# everything except dep changes.
COPY pyproject.toml uv.lock README.md ./
COPY package.json package-lock.json ./
COPY src ./src

# Install Python deps (production set only) into /app/.venv.
RUN uv sync --frozen --no-dev

# Install ccusage (and any other npm deps) from package-lock.json. The
# --omit=dev keeps the image lean; ccusage itself is in `dependencies`.
RUN npm ci --omit=dev

# ---- Stage 3: runtime — slim image with just the artefacts we need ----
FROM python:3.12.7-slim-bookworm AS runtime

COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
# npm/npx are not needed at runtime — ccusage is invoked directly via
# its locally-installed bin in node_modules. Skipping them keeps the
# runtime layer smaller and removes one CVE surface.

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/node_modules /app/node_modules
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY .streamlit /app/.streamlit

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

# Use python -m so we don't depend on a /app/.venv/bin/tokenscope shim
# being on PATH — works the same way `tokenscope` console script does
# (re-execs streamlit) but is explicit about what runs.
ENTRYPOINT ["python", "-m", "streamlit", "run", "src/tokenscope/app.py"]
