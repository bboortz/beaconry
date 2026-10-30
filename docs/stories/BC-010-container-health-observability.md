---
id: BC-010
title: Beaconry container health observability — four-endpoint convention
status: shipped
created: 2026-10-30
branch: feat/BC-010-container-health-observability   # created at step 2
plan: docs/plan/BC-010-container-health-observability-plan.md   # created at step 2
---

# BC-010: Beaconry container health observability

## Narrative

The homelab operator pulls up the beaconry Compose stack after a week away. Scout and hub are both "running" per `docker compose ps`. That's all they knew before BC-010 — "running" doesn't mean the grype DB is loaded, doesn't mean Telegram polling is alive, doesn't mean scout can reach the Docker socket. After BC-010, one command — `make status` — prints a one-line summary with every service's `/livez`, `/readyz`, `/startupz`, `/healthz` state plus degradation hints. Every beaconry container (hub, scout, future adapters) implements the four-endpoint convention from `memory/health-checks.md`. Compose wires `depends_on: service_healthy` so scout no longer POSTs to a hub whose grype DB is still downloading. Operator trust: "if beaconry's status is green, I know scanning is working; if it's degraded, I know exactly which piece is broken."

## Trigger

Operator wants to know "is beaconry working?" at any moment — on reboot, after a config change, after a crash, during the quiet hours when nothing's firing. Also an architectural trigger: BC-011's post-fix target-container health waterfall needs the same vocabulary (`/livez` / `/readyz`) applied to the containers beaconry restarts.

## Urgency

N/A — this is development/observability work, not a tier-triggered remediation.

## Expected outcome

- Hub exposes `/livez`, `/readyz`, `/startupz`, `/healthz` per `memory/health-checks.md`
- Scout exposes `/livez`, `/readyz`, `/startupz`, `/healthz` via file-sentinels in `/data/` (or HTTP — implementer's call, standard permits either)
- Compose healthcheck for each service points at `/readyz`; `start_interval: 2s` for fast startup detection; `start_period` tuned per service
- `scout` service in compose declares `depends_on: hub: {condition: service_healthy}` — scout waits for hub ready
- **`make status` is a new Makefile target** (does not exist today — sibling to the BC-007 `make findings` CLI). Backed by a new `poc/scripts/status.py` helper that curls each service's `/healthz`, aggregates, and prints a human-readable one-liner: `hub: healthy (grype_db ok, telegram polling, 7 findings) · scout: healthy (last scan 2m ago, 11 images)` or the degraded equivalent with the specific failing check named. Exits 0 when all services healthy, non-zero otherwise
- The restart-loop trap is **actively tested**: unit test monkeypatches the DB open to raise `OSError`, hits `/livez`, asserts 200. Monkeypatches Telegram app to `None`, hits `/readyz`, asserts 200. If either regresses in future, the gate fails
- Convention documented at `memory/health-checks.md` and @-included in `CLAUDE.md` so Claude sees it for every container built going forward

## Out of scope

- Target-container health check waterfall (post-fix verification of containers beaconry restarts) — that's **BC-011**, which inherits BC-010's vocabulary
- Kubernetes probe wiring — BC-010 adopts the k8s endpoint convention but we stay on Compose; k8s is a future migration
- Prometheus `/metrics` endpoint — tracked in IDEAS "Observability: structured logs + Prometheus metrics"
- JSON-structured application logs — same IDEAS entry
- Auto-rollback on health check failure — depends on BC-011
- Operator-configurable health policy per service — depends on BC-011
- Docker Hardened Images / sidecar healthcheck pattern — only relevant if we drop `python:3.12-slim`; parked IDEAS entry
- Public internet exposure of `/healthz` — endpoints remain internal (localhost / Compose network)

## Acceptance criteria (Gherkin)

```gherkin
Feature: Beaconry containers follow the four-endpoint health convention

  Background:
    Given the Compose stack is up with current grype DB cached

  Scenario: Hub /livez stays shallow — DB failure must not restart the pod
    Given sqlite3.connect raises OSError
    When GET /livez
    Then the response status is 200
    And no DB read is attempted

  Scenario: Hub /readyz reflects local prerequisites
    Given the DB file is writable, grype binary exists, grype DB file exists
    When GET /readyz
    Then the response status is 200
    When the grype DB file is removed
    And GET /readyz
    Then the response status is 503
    And the body names "grype_db" as the failing check

  Scenario: Hub /startupz gates during first run
    Given grype DB download has not completed
    When GET /startupz
    Then the response status is 503
    When grype DB download completes
    And GET /startupz
    Then the response status is 200 (and stays 200 for the lifetime of the container)

  Scenario: Hub /healthz is a side-effect-free JSON summary
    When GET /healthz
    Then the response status is 200
    And the body is JSON with keys {status, version, checks}
    And checks includes {db, grype_db, telegram, last_scan_s}

  Scenario: Telegram token failure degrades /healthz but not /livez or /readyz
    Given the Telegram bot token is revoked
    When GET /livez
    Then the response is 200
    When GET /readyz
    Then the response is 200
    When GET /healthz
    Then the body includes "telegram": "auth_failed"

  Scenario: Scout exposes the four indicators (file-sentinel or HTTP)
    Given scout is running
    Then the four indicators resolve as described in memory/health-checks.md § Scout analogue
    And `/data/scout.live` mtime is refreshed within the last SCAN_INTERVAL + 60s

  Scenario: Compose waits for hub healthy before starting scout
    Given a cold Compose up with no cached grype DB
    When scout container is created
    Then scout stays in `created` state until hub reports healthy
    And scout's first POST hits a hub whose grype DB is already loaded

  Scenario: make status reports every service
    Given hub and scout are running
    When the operator runs `make status`
    Then stdout includes one line per service naming its state plus the degraded checks, if any
    And the exit code is 0 if every service is healthy, non-zero otherwise
```

## Capabilities delivered

- **CAP-B015: Operator always knows if beaconry itself is working** (business, active) — new. `make status` is the one-stop answer.
- **CAP-T035: Hub implements the four-endpoint health convention** (technical, active) — new. `/livez`, `/readyz`, `/startupz`, `/healthz` per `memory/health-checks.md`.
- **CAP-T036: Scout implements the four-endpoint health convention via file-sentinels or HTTP** (technical, active) — new.
- **CAP-T037: Compose startup ordering uses `depends_on: service_healthy`** (technical, active) — new.
- **CAP-T038: Hub `/livez` and `/readyz` survive degraded external dependencies without restart-looping** (technical, active) — new. Enforces the restart-loop trap by test.
- **CAP-T039: `make status` prints an operator-readable summary of all beaconry services** (technical, active) — new; `make status` target does not exist today. Sibling to CAP-T025 (`make findings`). Both are read-only CLIs over the live Compose stack. If future surfaces (dashboard, Prometheus) land, they also deliver CAP-B015 without needing a new business CAP.

Supersedes: existing `/health` endpoint on hub (today's shallow stub) → replaced by the four-endpoint set. `/health` kept as a 200 alias during migration if needed, otherwise removed.

## Open questions

- (none — resolved during this conversation's 3-source research pass. All 9 clarification answers folded into sections above.)

## Links

- Convention: `memory/health-checks.md` (adopted by this story)
- Design pointer: `memory/docker-standard.md § Healthcheck patterns` (template that BC-010 updates)
- Related: BC-005 (compose stack this extends), BC-011 (target-container waterfall — IDEAS, depends on BC-010 vocabulary)
- Sources: devops-daily.com/posts/healthz-convention-origin, web-alert.io/blog/health-check-endpoint-design-livez-readyz-guide, lours.me/posts/compose-tip-008-dhi-healthcheck
