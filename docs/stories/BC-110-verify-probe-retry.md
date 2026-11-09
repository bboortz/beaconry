---
id: BC-110
title: Verify probe retry — handle post-restart port-binding race
status: in-progress
created: 2026-11-09
branch: feat/BC-110-verify-probe-retry
plan: docs/plan/BC-110-verify-probe-retry-plan.md
---

# BC-110: Hub retries probe before marking verification failed

## Narrative

Operator taps [🔧 Fix now] on a Telegram alert. `DockerAdapter.execute`
re-pulls the image, `docker compose up -d` recreates the container,
and the hub immediately fires the BC-011 health probe (TCP connect
on `:6379`, HTTP GET, or `docker inspect` on the container's own
HEALTHCHECK). Pre-BC-110: in the common case the service hasn't
finished binding its port yet (~100-500ms after container start),
the probe fails once, hub sets `verification_failed`, Telegram shows
`⚠️ Probe failed: tcp connect failed`, operator manually taps
`Verify` 10s later and it passes. False-failure UX.

BC-110 retries the probe up to `BEACONRY_VERIFY_PROBE_MAX_ATTEMPTS`
times (default 3) with exponential backoff
(`BASE_S × 2^(attempt-1)` — default 2s, 4s, 8s). The common race
clears within the first retry; pathological failures still fire
`verification_failed` after exhausting retries.

## Trigger

Operator taps `Verify` or `Fix now` → hub calls
`_waterfall.run_probe_with_retry(probe, ...)`.

## Expected outcome

- First-attempt success: unchanged — no retry, no delay.
- First-attempt failure + eventual success: probe passes within
  `MAX_ATTEMPTS`, result decorated with `(passed on attempt N/M)` so
  logs + Telegram show the retry worked.
- All attempts fail: last failure returned verbatim (same as
  pre-BC-110 behavior for genuine failures).

## Out of scope

- Retry on operator-tapped Verify when the finding is already
  `verification_failed` — that's a separate flow and the operator
  already got explicit confirmation of the failed state.
- Mid-probe cancellation (operator tapping anything else during the
  retry window). Probes are short-running (<30s even with 3× retries
  at BASE=2s).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Verify probe retries transient post-restart failures

  Scenario: Probe fails once then succeeds
    Given a fresh docker restart with port-binding delay
    When hub fires the probe
    Then the first attempt fails
    And hub sleeps base_s × 2^0 seconds
    And the second attempt succeeds
    And the reason reads "<original reason> (passed on attempt 2/3)"
    And status flips to fixed_verified

  Scenario: Backoff doubles each failed attempt
    Given MAX_ATTEMPTS is 4 and BASE_S is 1
    When all attempts fail
    Then sleep gaps are 1s, 2s, 4s (no sleep after the last attempt)

  Scenario: MAX_ATTEMPTS = 1 preserves single-shot behavior
    Given operator opts out of retry (MAX_ATTEMPTS=1)
    When the probe is called
    Then it runs exactly once with no sleep
```

## Capabilities delivered

- **CAP-T212: Verify-probe retry with exponential backoff.** Reduces
  false `verification_failed` flags on the healthy-service /
  port-binding-race common case.

## Links

- BC-011 (introduced the health probe waterfall).
- BC-108 / BC-109 (same backoff pattern — scout events watchdog,
  hub grype-db retry).
