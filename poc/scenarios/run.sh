#!/usr/bin/env bash
# BC-038: live-stack scenario harness lifecycle.
#
# Spins up an isolated hub (compose project `beaconry-scenarios`) with
# BEACONRY_TEST_MODE=1 + BEACONRY_MOCK=1 (skip grype DB), waits for /readyz,
# runs `python -m poc.scenarios`, tears down.
#
# Opt-out via `BEACONRY_SKIP_SCENARIOS=1` from the Makefile.
set -euo pipefail

cd "$(dirname "$0")/../.."

PROJECT=beaconry-scenarios
DATA_DIR=/tmp/${PROJECT}
HUB_PORT=8004
HUB_URL="http://127.0.0.1:${HUB_PORT}"
COMPOSE_FILE="poc/scenarios/compose.yml"

cleanup() {
  echo "==> tearing down compose project '$PROJECT'"
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$DATA_DIR"
}
trap cleanup EXIT

mkdir -p "$DATA_DIR"
chmod 777 "$DATA_DIR"

echo "==> starting isolated hub (test-mode) on port $HUB_PORT"
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --build

echo "==> waiting for /readyz…"
for _ in $(seq 1 60); do
  if curl -sf "${HUB_URL}/readyz" >/dev/null 2>&1; then
    echo "==> hub ready"
    break
  fi
  sleep 1
done

if ! curl -sf "${HUB_URL}/readyz" >/dev/null 2>&1; then
  echo "✗ hub never became ready"
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" logs hub | tail -40
  exit 1
fi

echo "==> running scenarios"
python3 -m poc.scenarios \
  --hub-url "$HUB_URL" \
  --data-dir "$DATA_DIR"
