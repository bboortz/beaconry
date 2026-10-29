#!/usr/bin/env bash
# Beaconry POC demo script
# Seeds nginx:1.24 (known CVEs), starts the stack, tails hub logs.
# Expected: Telegram notification within ~2min of hub startup.
#
# Flags:
#   --smoke   Run the CI smoke mode: build + up + scan without needing
#             real Telegram creds; asserts findings land in the hub.
#             (BC-000 ships a stub; real smoke implementation is BC-001.)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "${1:-}" == "--smoke" ]]; then
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    cd "$REPO_ROOT"
    PY="${PYTHON:-python3}"
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
        PY="$REPO_ROOT/.venv/bin/python"
    fi
    exec "$PY" -m poc.demo --non-interactive
fi

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.example and fill in Telegram credentials."
    echo "  cp .env.example .env && \$EDITOR .env"
    exit 1
fi

echo "==> Pulling demo target: nginx:1.24 (known-vulnerable image)"
docker pull nginx:1.24

echo ""
echo "==> Building and starting beaconry stack"
docker compose up --build -d

echo ""
echo "==> Waiting for hub to become healthy (grype DB download may take ~1min)…"
timeout 180 bash -c 'until docker inspect beaconry-hub --format "{{.State.Health.Status}}" 2>/dev/null | grep -q healthy; do sleep 3; printf "."; done'
echo " hub healthy"

echo ""
echo "==> Triggering immediate scan (instead of waiting for SCAN_INTERVAL)"
docker compose exec scout python3 scout.py --once

echo ""
echo "==> Tailing hub logs — watch for CVE findings and Telegram notification"
echo "    (Ctrl+C to stop)"
docker compose logs -f hub
