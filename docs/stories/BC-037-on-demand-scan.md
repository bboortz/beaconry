---
id: BC-037
title: On-demand scout scan trigger — `make scan`
status: shipped
created: 2026-10-31
branch: feat/BC-037-on-demand-scan
plan: docs/plan/BC-037-on-demand-scan-plan.md
---

# BC-037: On-demand scout scan trigger — `make scan`

## Narrative

Homelab operator at desk. Telegram pinged this morning: `nginx:1.24 → CVE-2026-X at CVSS 9.8`. Operator pulled the fix manually outside beaconry (`docker compose pull nginx && docker compose up -d`), confirmed the service is back up, and now wants to see **right now** whether beaconry still thinks nginx is vulnerable. Current best option: wait up to 300s for the next scout cycle. For a one-service touch-up that's acceptable; for a compose stack with 20 services the operator just rebuilt, 5 minutes of silence after a change is unnerving — did it work?

BC-033 already handles the Fix-button path (verify_fix re-scans the specific image via grype). BC-037 fills the manual-fix gap: operator runs `make scan`, scout immediately posts a fresh SBOM, hub re-processes, BC-031 reconcile auto-closes anything that's now clean, first-fires fire for anything new.

## Trigger

Operator runs `make scan` after any out-of-band change: `docker compose pull`, `docker compose up -d --build`, package upgrade, container restart.

## Expected outcome

- `make scan` returns within ~1 second with a success acknowledgement.
- Scout runs a fresh scan cycle in the background (same path as the 300s loop).
- Within ~90 seconds (grype matching) the DB reflects the new state: fixed findings auto-closed, new CVEs persisted + notified.
- Concurrent `make scan` while a scan is already running returns a clear "scan in progress" response — no double-post.
- No disruption to the background scan loop — the next scheduled cycle still fires on its own cadence.

## Out of scope

- Per-image targeted scan ("scan only nginx:1.24"). The existing Fix button covers single-image verification. V1 of this story triggers the full inventory cycle.
- Scheduling (`make scan --at 02:00`). Current scheduling lives in SCAN_INTERVAL.
- Multi-host trigger. One hub, one scout, local trigger.
- Authentication on the trigger endpoint. Scout's HTTP server already binds `127.0.0.1:8001` only; same trust model.

## Acceptance criteria (Gherkin)

```gherkin
Feature: On-demand scan trigger

  Scenario: `make scan` kicks a cycle and returns promptly
    Given beaconry stack is running
    When operator runs `make scan`
    Then the command returns within ~1 second with HTTP 202 Accepted
    And scout logs "manual scan triggered"
    And the next scout log line is "Scan starting — host: <hostname>"

  Scenario: Concurrent trigger while scan is in flight returns 409
    Given a scan cycle is currently in progress
    When operator runs `make scan` a second time
    Then the second call returns HTTP 409 Conflict with a "scan already running" message
    And only ONE scan is actually executed

  Scenario: Trigger does not disrupt the background loop
    Given SCAN_INTERVAL=300
    And operator triggered a manual scan at t=0
    When t advances by 300s
    Then the next scheduled scan still fires at ~t=300 (NOT postponed to t=300+manual_duration)

  Scenario: `make scan` with stack down
    Given beaconry-scout container is not running
    When operator runs `make scan`
    Then the command fails with a clear "scout not reachable" message
    And exit code is non-zero

  Scenario: Fresh-DB post-fix round-trip
    Given finding F exists at status=open on image img:1.0
    And operator pulls img:1.0 → img:2.0 externally
    When operator runs `make scan`
    Then within ~90s F.status transitions to fixed_verified via BC-031 reconcile
```

## Capabilities delivered

- **CAP-T081: On-demand scan trigger — POST /scan/trigger with concurrent-run guard** (technical, active) — scout exposes an idempotent trigger endpoint; Makefile target curls it with human-readable feedback.
- **CAP-B021: Operator verifies out-of-band fixes without waiting on scan cadence** (business, active) — closes the "did my docker compose pull work?" latency gap for manual remediation flows.

Extends: CAP-T019 (scout scan loop), CAP-T071 (BC-031 reconcile — benefits from fresh scans).

## Links

- Design: `DESIGN.md § Core pipeline`
- Related: BC-013 (scout HTTP server — this adds a route to it), BC-031 (reconcile — primary consumer of the triggered scan), BC-033 (verify_fix — single-image sibling path).
