---
id: BC-107
title: Scout POST retry queue — no SBOM silently dropped on hub outage
status: in-progress
created: 2026-11-09
branch: feat/BC-107-scout-post-retry-queue
plan: docs/plan/BC-107-scout-post-retry-queue-plan.md
---

# BC-107: Scout persists failed /scan POSTs and replays them

## Narrative

Operator runs scout on a laptop + two servers. Home ISP drops for 90
seconds mid-scan. Pre-BC-107: scout's `requests.post(HUB_URL/scan, ...)`
raises `RequestException`, scout's `collect_and_post` logs
`"Failed to post to hub"`, and the SBOM is gone forever — hub never
sees that cycle's findings. Next SCAN_INTERVAL (5 min default) scout
tries a fresh scan, but if the drop coincides with an operator install
burst (e.g. `pacman -Syu` + docker pull of three new images), the
findings from that specific window are lost even after connectivity
recovers.

BC-107 mirrors BC-104 on the scout side. On POST failure we persist
the SBOM payload into a tiny SQLite at `{DATA_DIR}/scout-state.db`,
and a background sweep replays queued posts with exponential backoff.
After five failed attempts we audit `scan_abandoned` and drop the
payload. `/healthz` exposes `pending_scans_queue_depth` so operators
can see if scout is accumulating backlog.

## Trigger

Scout's `collect_and_post` catches `requests.RequestException` from
the hub `/scan` POST (connection refused, timeout, 5xx, etc.).

## Expected outcome

- SBOM payload is persisted to `pending_scans` with exponential-backoff
  schedule (default: 30s → 60s → 120s → 240s → 480s).
- Background sweep replays it; on success → DELETE row, log
  `scan_delivered`.
- After `BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS` (default 5) failed
  attempts → DELETE row, log `scan_abandoned`.
- `/healthz` surfaces `pending_scans_queue_depth: N`.
- Across scout restart, queued rows survive (SQLite durability).

## Out of scope

- Heartbeat retry queue — heartbeat loop naturally retries every
  `SCOUT_HEARTBEAT_INTERVAL_S` (60s default); a dropped heartbeat
  only momentarily shows scout as "stale" in `/fleet` and
  self-recovers.
- Syft subprocess retry — a failed syft invocation on one image re-tries
  next SCAN_INTERVAL; the retry queue is for POST transport, not scan
  content.
- Scout-side audit log file (stdlib `logging` + structured messages
  suffice; hub's `audit` table is the durable record for
  operator-facing events).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scout retries failed /scan POSTs with persistent backoff

  Scenario: Hub is unreachable during POST
    Given scout has just finished a scan with an SBOM payload
    And the hub /scan endpoint times out
    When scout attempts to POST
    Then the payload is inserted into pending_scans with attempts=0
    And a scan_queued log event is emitted
    And /healthz reports pending_scans_queue_depth: 1

  Scenario: Hub recovers — retry sweep delivers queued SBOM
    Given a row exists in pending_scans with next_attempt_ts <= now
    And the hub /scan endpoint accepts POSTs
    When the retry sweep runs
    Then the payload is POSTed successfully
    And the row is deleted
    And a scan_delivered log event with retry_attempts=N is emitted

  Scenario: Max attempts exceeded — scan is abandoned
    Given a row has attempts = MAX_ATTEMPTS - 1
    And the hub /scan endpoint still fails
    When the retry sweep runs
    Then the row is deleted
    And a scan_abandoned log event is emitted
    And /healthz reports pending_scans_queue_depth: 0
```

## Capabilities delivered

- **CAP-T209: Scout-side POST retry queue with persistent backoff.**
  Symmetric to CAP-T206 (BC-104's hub-side notify retry queue) but on
  the scout → hub transport. Both halves of the hub ↔ scout wire now
  survive transient outages without silent data loss.

## Links

- BC-104 (predecessor — hub-side notify retry queue; same shape).
- CAP-T206 (companion capability).
