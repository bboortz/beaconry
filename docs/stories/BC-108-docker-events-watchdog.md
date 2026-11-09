---
id: BC-108
title: Docker events watchdog — reconnect on daemon restart
status: in-progress
created: 2026-11-09
branch: feat/BC-108-docker-events-watchdog
plan: docs/plan/BC-108-docker-events-watchdog-plan.md
---

# BC-108: Scout reconnects to docker events when the stream drops

## Narrative

Operator runs `systemctl restart docker` (e.g. to apply a daemon
config change) at 10:14. Before BC-108, scout's `docker.events()`
blocking iterator raises `requests.exceptions.ChunkedEncodingError`,
the single-shot `_blocking_consume()` catches the exception, logs
`"docker events stream ended"`, and the `run_in_executor` task ends
forever. From that moment until the next scout restart, every image
pull, container create, and container stop fires NO scan trigger —
scout falls back to the SCAN_INTERVAL (5 min default) + daily rescan
(02:00) cadence. Operator installs three new images at 10:15; CVE
alerts for them appear 5 minutes later instead of within seconds.

BC-108 wraps the consume in a reconnection loop with capped
exponential backoff (1s → 60s). Scout reconnects to the daemon as
soon as the socket is ready again; a `docker_events_reconnected`
log event with the attempt count fires on resume.

## Trigger

The blocking `docker.events()` iterator terminates — either via
exception (daemon restart, socket torn) or clean EOS.

## Expected outcome

- On stream exception: log the error, sleep `backoff_s`, re-subscribe.
- Backoff doubles up to `BEACONRY_DOCKER_EVENTS_BACKOFF_MAX_S` (60s default).
- On successful reconnect: `docker_events_reconnected` log with the
  attempt count + total offline duration.
- On clean EOS (scout shutdown): exit the loop gracefully.

## Out of scope

- Buffered replay of events missed during the outage — docker.events()
  has no replay semantics; the first post-reconnect scan tick
  reconciles via SBOM diff anyway.
- Alerting on prolonged outages (operator sees via `make status` that
  scout's heartbeat is still fresh but no events are firing — surfaces
  out-of-band). Future BC if signal quality warrants.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scout reconnects to docker events after daemon restart

  Scenario: Daemon restart mid-session
    Given scout is subscribed to docker events
    When the events stream raises an exception
    Then scout sleeps for backoff_s seconds
    And re-subscribes to docker.events()
    And emits docker_events_reconnected log with attempt=2 on success

  Scenario: Repeated reconnect failures grow backoff
    Given reconnection attempt 1 failed
    When reconnection attempt 2 is scheduled
    Then the backoff is 2× the previous
    And the backoff is capped at BEACONRY_DOCKER_EVENTS_BACKOFF_MAX_S

  Scenario: Clean shutdown exits the loop
    Given scout receives a cancellation signal
    When the events stream closes cleanly (no exception)
    Then the reconnect loop exits
    And no further reconnection attempts occur
```

## Capabilities delivered

- **CAP-T210: Scout docker-events watchdog with capped exponential
  backoff.** Keeps scout responsive to inventory changes across
  daemon restarts.

## Links

- BC-093 (predecessor — introduced docker events listener).
- BC-103 (widened filter to include destroy/die/delete).
