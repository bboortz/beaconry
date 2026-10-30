---
id: BC-017
title: Automated end-to-end BC-011 probe flow via isolated test compose stack
status: shipped
created: 2026-10-30
branch: feat/BC-017-e2e-probe-flow
plan: docs/plan/BC-017-e2e-probe-flow-plan.md
---

# BC-017: End-to-end BC-011 probe flow (no Telegram)

## Narrative

BC-011 was fully verified live tonight — but only once, with a human tapping buttons on a phone. That doesn't scale: every refactor of the handler, resolver, or probe primitives risks silently breaking the Verify flow in a way unit tests can't catch (they mock Telegram sends, the inline-import ImportError that bit us tonight slipped through for that reason). BC-017 adds an isolated test compose stack — `beaconry-hub-test` + `probe-target-test` under compose project name `beaconry-test` — and a `pytest` suite that drives the full probe flow via HTTP endpoints the hub exposes behind `BEACONRY_TEST_MODE=1`. One `make test-e2e` command, no Telegram credentials needed, every refactor gated.

## Trigger

Developer-facing: after shipping BC-011 end-to-end validated-by-hand, there's no automated signal if the handler, resolver, probe code, or policy parser silently regresses. First refactor would be a tap-session hostage situation.

## Urgency

N/A — regression protection, not an operator feature.

## Expected outcome

- `poc/docker-compose.test.yml` brings up an isolated test stack under compose project name `beaconry-test`:
  - `beaconry-hub-test` container (same image as production hub) on `127.0.0.1:9000:8000`
  - `probe-target-test` nginx container serving `/healthy` (200) and `/degraded` (503) routes
  - Separate network `beaconry-test` and separate `beaconry-test-data` volume
  - All names and ports distinct from the operator's running `beaconry-hub` / `beaconry-scout` so the two can co-exist on the same machine
- Hub exposes four `/__test__/*` endpoints when `BEACONRY_TEST_MODE=1`:
  - `POST /__test__/insert-finding` — write a finding row to the test DB
  - `POST /__test__/invoke-callback` — craft a fake `Update` + call `on_button_click`, capture terminal message
  - `GET /__test__/sent-messages` — fetch captured messages from the fake bot
  - `POST /__test__/reset` — clear DB + captured messages
- `BEACONRY_MOCK=1` in the test stack short-circuits Telegram wiring; captures replace real sends
- `poc/tests/e2e/test_BC_011_probe_flow.py` runs via `pytest -m e2e` or `make test-e2e`:
  - **Terminal state coverage**: all 4 (✅ Service healthy, ⚠️ Probe failed, ⏭️ No probe configured, ℹ️ Already fixed_verified)
  - **Image tag scenarios**: `postgres:latest`, `postgres:16-alpine`, `postgres:16.1.3-alpine`, `nginx:1.24`, `nginxproxy/nginx-proxy:alpine` — each with a matched or deliberately-unmatched policy
  - **Finding statuses**: fixtures create findings in all 6 states (`open`, `verification_failed`, `snoozed`, `accepted`, `fixed`, `fixed_verified`); sort rank + make findings behavior asserted via the hub's own findings query path
- `make test-e2e` orchestrates: build + start test stack → run pytest → tear down, with the test stack cleanly namespaced to not touch the operator's running compose stack
- 1 new CAP registered
- All BC-016 drift guards pass (test marker added to pyproject.toml, make target added to help, env vars in test compose propagated correctly)

## Out of scope

- Running against a *real* Telegram chat — that's BC-011's original live-verification
- Running against *real* syft/grype — mocked throughout the e2e flow
- Scout coverage in the test stack — BC-011 is hub-side only; scout tested separately
- Load testing / probe performance — functional correctness only
- UI/dashboard — BC-011 is CLI + Telegram; e2e tests the handler path, not display
- Cross-platform test runners — assume `docker compose` is available
- Pushing to CI — `make test-e2e` lives locally for V1; CI integration is a follow-up

## Acceptance criteria (Gherkin)

```gherkin
Feature: Automated end-to-end BC-011 probe flow

  Background:
    Given the test compose stack is up under project name `beaconry-test`
    And `beaconry-hub-test` is listening on 127.0.0.1:9000
    And `probe-target-test` serves /healthy (200) and /degraded (503)

  Scenario: isolated from the operator's normal stack
    When `docker ps` lists running containers
    Then container names are `beaconry-hub-test` + `probe-target-test`, not `beaconry-hub` + `beaconry-scout`
    And the test stack's volume is `beaconry-test_beaconry-test-data`, not `poc_beaconry-data`
    And the test stack binds to 9000, not 8000

  Scenario: ✅ Service healthy on a successful HTTP probe
    Given a policy "postgres:latest=http:http://probe-target-test/healthy"
    And a finding on `postgres:latest` in status `fixed`
    When the test invokes the verify callback
    Then the captured terminal message contains "Service healthy — probe passed"
    And the DB status for that finding transitions to `fixed_verified`

  Scenario: ⚠️ Probe failed on a 5xx HTTP response
    Given a policy pointing at /degraded
    And a finding on `nginx:1.24` in status `fixed`
    When the test invokes the verify callback
    Then the captured terminal message contains "Probe failed"
    And the message includes "docker tag" rollback hint
    And the DB status transitions to `verification_failed`

  Scenario: ⏭️ No probe configured
    Given a finding on an image not in BEACONRY_HEALTH_PROBES
    When the test invokes the verify callback
    Then the captured terminal message contains "No probe configured"
    And the DB status is unchanged

  Scenario: ℹ️ Already fixed_verified (idempotence)
    Given a finding already in `fixed_verified` with a matching policy
    When the test invokes the verify callback
    Then the captured terminal message starts with "Already fixed_verified"
    And no probe runs (no network request to probe-target-test)
    And the DB status is unchanged

  Scenario: Common image tag scenarios resolve correctly
    Given policies for `postgres:latest`, `postgres:16-alpine`, `postgres:16.1.3-alpine`
    When each is probed via the verify callback
    Then each resolver call returns the matching policy
    And each probe hits the configured target (no :latest fallback contamination)

  Scenario: All six finding statuses round-trip through the DB
    Given findings created in each of (open, verification_failed, snoozed, accepted, fixed, fixed_verified)
    When the hub's SELECT returns them
    Then all 6 statuses are present
    And STATUS_RANK sort places `verification_failed` before `snoozed`
    And places `fixed_verified` after `fixed`

  Scenario: `make test-e2e` orchestrates the whole thing cleanly
    When operator runs `make test-e2e`
    Then test stack is built + started + waited-for-healthy
    And pytest runs against localhost:9000
    And test stack is torn down (containers + volumes removed) regardless of pass/fail
    And no beaconry-hub (operator's normal stack) container is affected
```

## Capabilities delivered

- **CAP-T054: Automated end-to-end BC-011 probe flow via `make test-e2e`** (technical, active) — new. Covers all 4 terminal states, all 6 finding statuses, 3+ common tag scenarios without needing Telegram credentials. Isolated test compose stack under separate project name + network + volume. Hub exposes `/__test__/*` endpoints only when `BEACONRY_TEST_MODE=1`. Make target orchestrates up→test→tear-down.

Supersedes partial coverage: unit tests + the one-shot Telegram verification sessions. Live-tap sessions remain valuable for human-UX validation; BC-017 is the regression gate between tap sessions.

## Open questions

- (none — scope resolved in the "scope expansion" discussion)

## Links

- Depends on: BC-011 (handler + probe + resolver code under test), BC-015/016 (drift guards must not fail when the new compose file + pytest marker land)
- Related: BC-003 (handler path tests), BC-013 (scout live probes — a sibling e2e story if the pattern catches on)
