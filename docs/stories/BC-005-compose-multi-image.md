---
id: BC-005
title: Multi-image Compose demo — full pipeline containerised
status: shipped
created: 2026-10-29
branch: feat/BC-005-compose-multi-image
plan: docs/plan/BC-005-compose-multi-image-plan.md
---

# BC-005: Multi-image Compose demo — full pipeline containerised

## Narrative

The homelab operator wants to demo beaconry to a friend from a laptop that has nothing but Docker + Docker Compose. They clone, fill `poc/.env` with Telegram creds, and type `make demo`. Under the hood this now runs `docker compose up --build` — scout and hub spin up as containers, no local Python required. Scout scans every Docker image on the host whose name matches the allowlist (`nginx*, redis*, postgres*` by default — safe demo scope, excludes dev/test clutter). Hub applies the BC-002 cap + CVE dedup across the full image inventory and real Telegram messages arrive. `BEACONRY_MOCK=1 make demo` continues to run the fast local-Python mock pipeline for dev inner loop.

## Trigger

Operator first wants beaconry on their actual stack, not a hard-coded image.

## Expected outcome

- `make demo` (real mode) runs `cd poc && docker compose up --build` in foreground
- Scout container scans every image whose tag matches `SCOUT_INCLUDE` glob (default `nginx*,redis*,postgres*`) and doesn't match `SCOUT_EXCLUDE` glob (default empty)
- Hub applies the BC-002 cap + dedup across the full inventory
- Scout mount of `/var/run/docker.sock` is `:ro`; hub keeps `rw` for pulls
- `make demo` with `BEACONRY_MOCK=1` continues to run the local-Python mock pipeline (BC-002 path unchanged)
- `docker compose down` tears down cleanly; grype DB cache survives in the `beaconry-data` named volume
- No host-side Python / syft / grype required for the real-mode demo
- Operator with no matching images sees a clear "0 images matched SCOUT_INCLUDE, adjust the glob" log line (fail-friendly)

## Out of scope

- Auto-fix execution (BC-003+)
- Button handling re-verified under Compose (same callback code paths; BC-003 draft owns live-verification)
- Multi-host (one scout per host; BC-005 is single-host-many-images)
- ARM / Pi
- Bootstrapping Docker itself
- A Compose-smoke mode (mocked HTTP)
- Scanning non-Docker inventory (pacman, pip) — scout code supports it but demo focuses on Docker

## Acceptance criteria (Gherkin)

```gherkin
Feature: Compose-based multi-image real pipeline demo

  Background:
    Given docker and docker compose are installed
    And poc/.env contains valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
    And at least one image matching the SCOUT_INCLUDE allowlist is pulled

  Scenario: `make demo` runs the whole pipeline containerised
    When operator runs "make demo"
    Then the Makefile invokes "docker compose up --build" under poc/
    And hub and scout containers both reach a healthy state
    And within 5 minutes a Telegram message arrives naming at least one
        real CVE from a real matching image
    And no python / syft / grype is required on the host

  Scenario: Scout respects the SCOUT_INCLUDE allowlist
    Given SCOUT_INCLUDE="nginx*,redis*" in poc/.env
    And the host has nginx:1.24, redis:7, and busybox:latest pulled
    When the stack starts
    Then scout logs show nginx:1.24 and redis:7 were scanned
    And scout logs show busybox was skipped (does not match include glob)

  Scenario: SCOUT_EXCLUDE narrows further
    Given SCOUT_INCLUDE="*" and SCOUT_EXCLUDE="*-test,*-dev" in poc/.env
    When the stack starts
    Then scout logs show no "*-test" or "*-dev" image was scanned

  Scenario: Per-run cap applies across the full inventory
    Given scout surveys 10 matching images producing 50 unique CVEs
    And BEACONRY_MAX_FINDINGS_PER_RUN=5
    When scout posts to hub
    Then at most 5 individual Telegram messages are sent
    And 1 digest message summarises the remaining 45

  Scenario: Scout mount of docker socket is read-only
    Given the stack is running
    When "docker inspect beaconry-scout" is run
    Then the mount for /var/run/docker.sock reports "RO: true"

  Scenario: `BEACONRY_MOCK=1 make demo` still runs the local-Python mock
    When operator runs "BEACONRY_MOCK=1 make demo"
    Then docker compose is NOT invoked
    And the BC-001/BC-002 mock pipeline runs locally
    And exit code is 0 within 10 seconds

  Scenario: No matching images → fail-friendly guidance
    Given SCOUT_INCLUDE="imagethatdoesnotexist*"
    When the stack starts
    Then scout logs contain "0 image(s) matched SCOUT_INCLUDE"
    And include a suggestion to adjust the glob

  Scenario: Teardown is clean; grype DB survives
    Given the stack has been running
    When "docker compose down" is run
    Then no beaconry-* containers remain
    And the beaconry-data volume still contains the grype DB

  Scenario: Hub health endpoint responds
    Given the stack is running
    When "curl -s localhost:8000/health" is called
    Then the response is {"status":"ok"}

  Scenario: Integration test (opt-in, 'compose' marker)
    When "pytest -m compose" is invoked
    Then it skips unless docker compose is available
    And when run, it builds + starts the stack, mocks Telegram at
        hub.notify, asserts at least one Finding is saved to the dev DB,
        and tears down cleanly
```

## Capabilities delivered

- **CAP-B009: Operator runs the full demo pipeline with Docker only (no Python toolchain required)** (business, aspirational → active) — new.
- **CAP-B010: Scout surveys the full Docker inventory with glob allow/deny filtering** (business, aspirational → active) — new.
- **CAP-T018: Scout filters image tags by include/exclude glob patterns** (technical, active) — new.
- **CAP-T019: Compose stack brings up hub + scout with correct :ro socket mount, healthchecks, env propagation** (technical, active) — new.
- **CAP-T020: Hub import suppresses noisy HTTP client loggers so bot tokens don't leak** (technical, active) — new (added in fix branch after live-run surfaced the leak).
- **CAP-T021: Hub /scan endpoint applies dedup + cap across the full inventory** (technical, active) — new (added in fix branch after live-run showed ~100-message flood).

Extends: CAP-B002 (notification arrives) now cross-image; CAP-B004 (not flooded) now applies across the full inventory.

## Open questions

- (none — resolved during 2026-10-29 clarification + 3 virtual-user iterations)

## Links

- Design: `DESIGN.md § Architecture`, `memory/docker-standard.md`
- Prior: BC-002 (notification pipeline), BC-004 (traceability — this story will register new CAPs)
