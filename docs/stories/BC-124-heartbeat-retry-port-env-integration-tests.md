---
id: BC-124
title: Heartbeat retry queue + HUB_HTTP_PORT env + BC-117/119 integration tests
status: shipped
created: 2026-11-16
branch: feat/BC-124-heartbeat-retry-port-env-integration-tests
plan: docs/plan/BC-124-heartbeat-retry-port-env-integration-tests-plan.md
---

# BC-124: Three reliability fixes split off from BC-123

## Narrative

Three items from the deeper-investigation audit that are independent
of BC-123's data-path visibility work:

1. Scout's `/heartbeat` POST has no retry. `/scan` got BC-107's
   persistent retry queue; heartbeat is still one-shot. A transient
   network blip during the 60s heartbeat tick drops the keepalive,
   and the hub thinks the scout is stale until the next tick.
2. The hub's uvicorn bind port is hardcoded to 8000 in the `__main__`
   block. Scout has `SCOUT_HTTP_PORT`; the hub doesn't. Local dev
   with multiple hub processes (parallel test runs, shadow instances)
   has no env knob.
3. BC-117 (cross-repo) and BC-119 (gzip compression) shipped with
   unit tests only. BC-120 + BC-122 have integration tests that
   catch wiring regressions the per-unit mocks can't. Backfill the
   gap so the bundle is consistent.

## Trigger

- Every heartbeat tick (automatic reliability improvement).
- Operator setting `HUB_HTTP_PORT=8002` for a parallel hub.
- Any dev touching the cross-repo or gzip paths.

## Expected outcome

- Scout retries a failed heartbeat up to `BEACONRY_HEARTBEAT_RETRY_MAX_ATTEMPTS`
  times with capped exponential backoff.
- Hub respects `HUB_HTTP_PORT` env (default 8000).
- `pytest poc/tests/integration/test_BC_117_cross_repo_cve_fix_workflow.py`
  and `poc/tests/integration/test_BC_119_scout_scan_gzip_compression.py`
  exist and pass.

## Out of scope

- Heartbeat persistence across scout restart. `/scan` has that via
  BC-107's sqlite queue; `/heartbeat` payload is tiny (< 200 bytes)
  and losing it on restart is acceptable — the next tick fills the
  gap anyway. In-memory retry only.
- Hub port propagation into the Dockerfile EXPOSE directive — stays
  8000 there; env changes are for `uvicorn.run(port=…)` only.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Reliability + test coverage backfill

  Scenario: Heartbeat retries on transient network failure
    Given scout's first POST /heartbeat raises ConnectionError
    When _heartbeat_with_retry runs
    Then scout sleeps base × 2^attempt seconds and retries
    And up to BEACONRY_HEARTBEAT_RETRY_MAX_ATTEMPTS total attempts
    And an audit-level WARN is logged on each failure

  Scenario: Hub respects HUB_HTTP_PORT env
    Given HUB_HTTP_PORT=8002 is set
    When the hub starts
    Then uvicorn binds on 8002 (not the 8000 default)
    And /healthz is reachable on the new port

  Scenario: BC-117 integration test exists
    Given poc/tests/integration/test_BC_117_cross_repo_cve_fix_workflow.py
    When pytest collects and runs it
    Then the end-to-end cross-repo path is exercised against a real hub TestClient

  Scenario: BC-119 integration test exists
    Given poc/tests/integration/test_BC_119_scout_scan_gzip_compression.py
    When pytest collects and runs it
    Then a real gzipped body round-trips through the hub's middleware
```

## Capabilities delivered

- **CAP-T226: Heartbeat retry with capped exponential backoff**
  (type: technical, status: active)
  *Mirrors BC-107's /scan retry but in-memory only (payload is
  tiny, loss-tolerant). `_heartbeat_with_retry` wraps the existing
  `heartbeat_once` with a loop that catches `requests.RequestException`,
  sleeps `BEACONRY_HEARTBEAT_RETRY_BASE_S × 2^attempt`, and gives up
  after `BEACONRY_HEARTBEAT_RETRY_MAX_ATTEMPTS`. Defaults 3 / 2s
  (matches BC-112 scan-trigger retry cadence).*
- **CAP-T227: Hub HTTP port env knob**
  (type: technical, status: active)
  *`HUB_HTTP_PORT` env var read at module import; the `__main__`
  dispatch passes it to `uvicorn.run(port=…)`. Default 8000.*

Extends CAP-T153 (scout heartbeat loop) and CAP-T223 (pipeline
integrity test coverage).

## Links

- Related: BC-107 (scan retry queue), BC-112 (scan-trigger proxy
  retry), BC-117, BC-119.
