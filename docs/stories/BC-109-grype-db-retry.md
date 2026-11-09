---
id: BC-109
title: Grype DB update retry — hub recovers from transient network failure
status: shipped
created: 2026-11-09
branch: feat/BC-109-grype-db-retry
plan: docs/plan/BC-109-grype-db-retry-plan.md
---

# BC-109: Hub keeps trying the grype DB download until it succeeds

## Narrative

Operator restarts the hub at 03:12 while the home ISP is mid-flap.
Pre-BC-109, hub's lifespan runs `grype db update` once; the download
times out at ~15MB of the 200MB payload, hub logs
`"grype db update warning: ..."`, sets `_startup_complete = True`,
and begins serving requests. `/readyz` says `grype_db: empty` → the
container shows unhealthy to compose → scout's
`depends_on: service_healthy` blocks downstream. The network comes
back 90 seconds later but nothing in hub tries the download again.
Operator finds out next morning that no scans have landed overnight
and has to `docker restart beaconry-hub`.

BC-109 wraps the download in a retry loop with capped exponential
backoff (10s → 600s cap). On startup failure, a background task
takes over; it keeps retrying until the download succeeds, at which
point `/readyz` turns green on its own and the stack recovers
without operator intervention.

## Trigger

Hub lifespan's `grype db update` subprocess exits non-zero OR raises
on spawn (binary missing, permissions).

## Expected outcome

- First-attempt success: startup unchanged (log + continue).
- First-attempt failure: `_grype_db_update_retry_loop_forever` task
  is created. It sleeps `BEACONRY_GRYPE_DB_RETRY_BASE_S` (default 10s),
  retries, and doubles the backoff up to `BEACONRY_GRYPE_DB_BACKOFF_MAX_S`
  (default 600s) until the download succeeds.
- `/readyz` stays 503 for the `grype_db` probe (empty dir) during
  failure — no change from pre-BC-109; just now has an auto-recovery
  path.
- `/healthz` exposes `grype_db_update_attempts`,
  `grype_db_last_update_ts`, `grype_db_last_error` so operators see
  the retry drift at a glance.
- On success, the loop exits.

## Out of scope

- Mid-session DB refresh (grype has its own `db update` schedule for
  subsequent CVE data; BC-109 only guards the startup download).
- Alerting when retries persist beyond a threshold (operator sees via
  `/healthz` + `make status`; future BC if signal quality warrants).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Hub recovers from transient grype DB download failure

  Scenario: First-attempt failure triggers background retry loop
    Given the network is flaky
    When hub starts and `grype db update` returns non-zero
    Then hub logs the failure
    And a background retry task is spawned
    And `_startup_complete` is still set to True
    And /readyz continues to return 503 for grype_db (empty dir)

  Scenario: Retry eventually succeeds
    Given the background retry task is running
    When a retry attempt succeeds
    Then the loop exits
    And /readyz begins returning 200 for grype_db

  Scenario: Backoff doubles each failed retry
    Given the retry task has failed N times
    When the next retry is scheduled
    Then the backoff is 2 × the previous
    And the backoff is capped at BEACONRY_GRYPE_DB_BACKOFF_MAX_S
```

## Capabilities delivered

- **CAP-T211: Hub grype-db update retry with capped exponential
  backoff.** Mirror of BC-108 (scout docker-events watchdog) on the
  hub side — no permanent hub degradation on transient network
  failure during `grype db update`.

## Links

- BC-018 (introduced single-shot `grype db update` in lifespan).
- BC-108 (same retry-with-backoff pattern on scout docker events).
