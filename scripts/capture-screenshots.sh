#!/usr/bin/env bash
#
# Capture screenshots of the unified tokenscope Grafana dashboard for the
# README. Requires:
#   - The full docker compose stack running (cd docker && docker compose up)
#   - The grafana-image-renderer plugin installed (default in docker-compose.yml)
#   - curl
#
# Output: docs/images/dashboard-*.png
#
# Usage:
#   scripts/capture-screenshots.sh           # default: dark theme, 1600x2400
#   GRAFANA_URL=... scripts/capture-screenshots.sh   # override Grafana URL

set -euo pipefail

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
GRAFANA_USER="${TOKENSCOPE_GRAFANA_USER:-admin}"
GRAFANA_PASSWORD="${TOKENSCOPE_GRAFANA_PASSWORD:-tokenscope}"
DASHBOARD_UID="tokenscope"
THEME="dark"
WIDTH=1600
HEIGHT=2400
OUTPUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docs/images"

mkdir -p "$OUTPUT_DIR"

echo "Capturing tokenscope dashboard from ${GRAFANA_URL} (theme=${THEME})..."

# Render full dashboard
curl -sS \
  -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
  --output "${OUTPUT_DIR}/dashboard-full.png" \
  --write-out 'http_status=%{http_code} bytes=%{size_download}\n' \
  "${GRAFANA_URL}/render/d/${DASHBOARD_UID}/tokenscope?orgId=1&from=now-24h&to=now&theme=${THEME}&width=${WIDTH}&height=${HEIGHT}&kiosk=tv"

# Validate that we got a PNG, not a Grafana login page or error
if file "${OUTPUT_DIR}/dashboard-full.png" | grep -q 'PNG image'; then
  echo "✓ ${OUTPUT_DIR}/dashboard-full.png ($(wc -c < "${OUTPUT_DIR}/dashboard-full.png") bytes)"
else
  echo "✗ dashboard-full.png is not a PNG. Likely the image renderer plugin is not installed."
  echo "  Check: docker exec tokenscope-grafana grafana cli plugins ls | grep image-renderer"
  echo "  And:   docker logs tokenscope-grafana | grep -i 'render\\|chromium'"
  exit 1
fi

echo "Done. Screenshots in ${OUTPUT_DIR}/"
