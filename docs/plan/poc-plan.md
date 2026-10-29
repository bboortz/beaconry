# POC — Plan

*Branch: `poc/` directory (no dedicated branch — built on main)*
*Start date: 2026-10-29*

---

## Situation

Beaconry has a full DESIGN.md but no running code. The POC proves the core loop end-to-end on a single host before building anything else.

---

## Problems to solve

- No concrete evidence the syft → grype → Telegram → executor loop works
- Can't show anyone the idea without something running
- Architecture choices (pluggable executors, scout read-only) unproven in code

---

## Goals

1. Scout collects Docker image SBOMs via syft and posts to hub
2. Hub runs grype against each SBOM, filters CVSS ≥ 7
3. Hub sends Telegram notification: CVE ID + severity + host + fix command + description
4. Telegram [Fix now] button triggers DockerAdapter: pull latest, re-scan, confirm
5. [Snooze] and [Accept risk] buttons update finding status in SQLite
6. Full loop provable with `docker pull nginx:1.24` as the seeded CVE

---

## Scope

- `poc/scout/` — Python scout using syft for Docker images + pacman DB parser
- `poc/hub/` — Python/FastAPI hub: grype match + Telegram bot + SQLite state
- `poc/hub/executor/` — base class + DockerAdapter + ManualAdapter
- `poc/docker-compose.yml` — brings both up with one command
- `poc/demo.sh` — seeds nginx:1.24, starts stack, watches for notification

---

## Non-goals

- CISA KEV feed integration (V2)
- Exposure scoring (V2 — POC uses raw CVSS threshold)
- Multi-host canary ring (V2)
- Auto-rollback on health check failure (V2 — POC notifies but doesn't revert)
- Auto-update vulnerability DB (hub runs `grype db update` at startup only)
- Pacman SBOM scanning (included but secondary to Docker demo)

---

## Design decisions

**Scout is read-only**: generates SBOMs, POSTs to hub. No execution path in scout.

**Executor is pluggable**: `ExecutorPlugin` abstract base class. `DockerAdapter` and `ManualAdapter` for POC. Hub selects first adapter that `can_handle(finding)`.

**State in SQLite**: finding deduplication (INSERT OR IGNORE on stable ID). Status: open → fixed / snoozed / accepted.

**Finding ID**: `sha256(host:cve_id:source_ref:package)[:16]` — stable across scans.

**Health check**: after DockerAdapter pulls latest, hub runs syft+grype on the new image to verify CVE is gone. Reports result in Telegram.

**Pacman SBOM**: parse `/host/var/lib/pacman/local/` directly (no pacman binary needed in container). Build syft-compatible JSON with `type: alpm` artifacts.

---

## Testing strategy

1. Seed: `docker pull nginx:1.24` on the host before starting the stack
2. `docker compose up` — scout scans and posts within SCAN_INTERVAL (default 60s for POC)
3. Telegram notification appears for nginx:1.24 CVE(s)
4. Tap [Fix now] — watch hub log for grype re-scan
5. Telegram message updates to "Fixed and verified" or "CVE still present"

Manual verification: `grype nginx:1.24 -o table` on the host before and after.

---

## Testing commands

```bash
cd poc/

# 1. Seed the demo target
docker pull nginx:1.24

# 2. Configure
cp .env.example .env
# edit .env: add TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID

# 3. Start
docker compose up --build

# 4. Trigger manual scan (instead of waiting for SCAN_INTERVAL)
docker compose exec scout python3 scout.py --once

# 5. Verify grype finds CVEs in the image
grype nginx:1.24 -o table | head -20
```

Expected: Telegram notification within ~2 minutes of first startup (grype DB download + scan).
