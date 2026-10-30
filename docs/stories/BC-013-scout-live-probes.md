---
id: BC-013
title: Scout live HTTP probes + unified `make status` table display
status: shipped
created: 2026-10-30
branch: feat/BC-013-scout-live-probes
plan: docs/plan/BC-013-scout-live-probes-plan.md
---

# BC-013: Scout live HTTP probes + unified `make status` table display

## Narrative

The homelab operator runs `make status` and sees a wall of mixed hub-and-scout text with technical error strings like `URLError` and `no scout.healthz.json; container not up?` — fine for debugging, hostile for daily operations. Scout's health is still a historical snapshot read from a file that scout wrote the last time its loop finished, with a staleness window of up to 5 minutes. BC-013 promotes scout to live HTTP probes (same convention hub uses) and reformats `make status` into a Docker-Compose-ps-shaped table — one row per container, named columns, no raw tracebacks, human-readable even when the stack is down.

## Trigger

Developer-facing: the asymmetry noticed in BC-010's post-ship audit (`make status` queries hub live, reads scout from snapshot). Operator-facing: when the compose stack is not up, current output reads *"hub: unreachable (URLError) · scout: unreachable (no scout.healthz.json; container not up?)"* — four technical words operators shouldn't need to parse.

## Urgency

N/A — observability / developer-productivity.

## Expected outcome

- Scout runs a tiny FastAPI HTTP server on internal port 8001, serving `/livez /readyz /startupz /healthz` via `beaconry_health.build_router` (BC-012 primitives) with scout-specific check providers
- Compose exposes `127.0.0.1:8001:8001` for `make status` to query from the host
- Scout's compose healthcheck targets `/readyz` HTTP (replacing the file-sentinel test). SentinelWriter class stays in `beaconry_health` as a public option for future HTTP-less loop services, but scout stops using it.
- `make status` queries both services uniformly via HTTP — one code path, one timeout, no `docker exec` special case, no `SCOUT_LIVE_MAX_AGE_S` env knob
- `make status` prints a column-aligned table — NAME, STATE, HEALTH, LAST SCAN, DETAILS — with user-friendly phrasing for every state (down, absent, starting, degraded, healthy). No raw Python exception class names in the output.
- Exit 0 all-healthy, 1 any-degraded, 2 any-container-down. Three exit codes operators can grep on.
- All BC-010 behavior preserved (hub endpoints unchanged, trap guard unchanged); BC-012 primitives reused (not reimplemented) for scout.

## Out of scope

- Target-container health check waterfall (BC-011) — orthogonal; BC-013 is beaconry's own observability, not target probing
- Prometheus `/metrics` endpoint on scout — natural follow-up but a separate IDEAS entry
- Multi-host scout coordination (scout-to-scout) — large unknowns, parked
- Removing SentinelWriter from `beaconry_health` — kept as a public option for HTTP-less services
- Publishing scout's port to the public internet — stays 127.0.0.1-bound
- Operator-configurable scout port (hardcoded 8001 for V1)
- Rewriting `make findings` to match the new table format — different story if we want table uniformity across CLIs

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scout exposes live HTTP health probes + make status prints a table

  Scenario: Scout serves /livez /readyz /startupz /healthz via build_router
    Given scout starts with FastAPI app + build_router wired to scout-specific providers
    When each of /livez /readyz /startupz /healthz is GET'd
    Then responses match the BC-010 four-endpoint contract shape
    And /livez returns 200 even when Docker socket is unavailable (restart-loop trap honored)

  Scenario: Scout's compose healthcheck targets /readyz
    Given the Compose config
    When docker compose config --format json is parsed
    Then scout's healthcheck test string references `/readyz`
    And scout no longer tests scout.ready / scout.live sentinel files

  Scenario: make status queries scout via HTTP uniformly with hub
    When make status runs against a running Compose stack
    Then status.py makes one urllib.request to hub /healthz and one to scout /healthz
    And no docker exec is invoked
    And the SCOUT_LIVE_MAX_AGE_S env knob no longer exists in status.py

  Scenario: make status prints a column-aligned table
    When make status runs
    Then stdout contains a header row: NAME, STATE, HEALTH, LAST SCAN, DETAILS
    And one row per container (beaconry-hub, beaconry-scout)
    And every cell has a human-readable value — no "URLError", no "<class 'ConnectionError'>", no tracebacks

  Scenario: make status handles stack-down cleanly
    Given no beaconry containers are running
    When make status runs
    Then stdout prints the same table with STATE=down for both containers
    And DETAILS says "compose stack not up — try `cd poc && docker compose up -d`" on the first down row
    And exit code is 2

  Scenario: make status handles partial-up state cleanly
    Given hub is running healthy but scout is stopped
    When make status runs
    Then hub row shows STATE=running HEALTH=healthy
    And scout row shows STATE=down DETAILS="container not up"
    And exit code is 2

  Scenario: make status surfaces hub degradation in DETAILS
    Given hub's grype DB is missing
    When make status runs
    Then hub row shows HEALTH=degraded
    And DETAILS names the failing check: "grype_db missing"
    And exit code is 1

  Scenario: Detection latency on scout crash drops from 5min to seconds
    Given scout is running healthy
    When scout is SIGKILL'd
    And make status runs ~2 seconds later
    Then scout row shows STATE=stopped or down (not "healthy")
    And no false-positive "healthy" is reported within the file-sentinel staleness window
```

## Capabilities delivered

- **CAP-B017: Operator sees beaconry stack status at a glance in a Docker-compose-ps-like table** (business, active) — new. Columns, human phrases, no raw tracebacks. Covers daily "is beaconry working?" with one glance.
- **CAP-T043: Scout serves /livez /readyz /startupz /healthz via FastAPI + beaconry_health.build_router** (technical, active) — new. Uniformity with hub, reusing BC-012 primitives.
- **CAP-T044: `make status` queries both services uniformly via HTTP; no docker exec special case** (technical, active) — new. Deletes `_fetch_scout_body_via_docker` + `SCOUT_LIVE_MAX_AGE_S` + `SCOUT_DATA_DIR` from status.py.
- **CAP-T045: `make status` output follows a column format with human-readable error phrasing** (technical, active) — new. Separates "which container is broken" from "how is it broken" visually.

Supersedes partially: CAP-T036 (scout file-sentinels) — scout stops using them; SentinelWriter class stays in beaconry_health as a public option for future HTTP-less services.

## Open questions

- (none — resolved during BC-010 post-ship architectural discussion plus BC-012 close-gap work)

## Links

- Convention: `memory/health-checks.md` (BC-013 brings scout fully into the convention)
- Depends on: BC-012 (shared `beaconry_health/`)
- Related: BC-010 (hub side of the same convention), BC-011 (target waterfall — orthogonal, uses same primitives)
