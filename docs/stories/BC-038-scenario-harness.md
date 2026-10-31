---
id: BC-038
title: Live-stack scenario harness — `make test-scenarios`
status: shipped
created: 2026-10-31
branch: feat/BC-038-scenario-harness
plan: docs/plan/BC-038-scenario-harness-plan.md
---

# BC-038: Live-stack scenario harness — `make test-scenarios`

## Narrative

Beaconry has deep unit coverage (90%+) and a BC-017 e2e stack for the probe waterfall. What it does **not** have is a way to verify that pipeline *behaviors* under state mutations match the operator-facing promises in `memory/design-decisions.md` and the shipped BCs. BC-030 landed internet_facing detection; the DB wipe + fresh ingest we did manually to prove it was ad-hoc, non-reproducible, and left no artifact.

Operator at desk, after any BC ships: *"I want one command that proves every operator-visible promise still works end-to-end — first-fire, auto-close, regression, snooze, digest cadence, heartbeat, Tier 0/1 differentiation, multi-host isolation — against the real hub binary."* That's the gate this story builds.

## Trigger

Operator (or CI) runs `make test-scenarios` after any ingest-pipeline change — BC-030-sized changes especially.

## Expected outcome

- `make test-scenarios` spins up a hub-only compose stack (no scout — all SBOMs come via test endpoint).
- Runs 10 scenarios in sequence, each wiping DB + audit log between.
- Each scenario mutates state via `POST /test/scan` (pre-matched candidates, no grype) + asserts via SQL + audit log.
- Pass/fail output, exits non-zero on first failure.
- Full suite completes in < 30 s on dev laptop.

## Out of scope

- Scenarios that depend on real grype matching. Determinism beats fidelity for this harness — grype coverage lives in the live `make demo` + the operator's own `make scan` loop.
- Covering every edge case of every BC. Start with the operator-facing promises; expand case-by-case as bugs surface.
- Replacing `test-e2e` (BC-017). That gate verifies BC-011 probe plumbing — different scope, kept separate.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scenario harness

  Scenario: `make test-scenarios` runs the scenario suite and reports results
    Given a clean workspace
    When operator runs `make test-scenarios`
    Then an isolated hub compose stack is started
    And 10 scenarios run in sequence
    And output shows one line per scenario with ✓ or ✗
    And exit code is 0 if all pass, non-zero if any fail

  Scenario: Test-mode endpoints are 404 in production mode
    Given BEACONRY_TEST_MODE is unset or 0
    When any `/test/*` endpoint is POSTed to
    Then the response is 404 Not Found

  Scenario: Scenario 01 — fresh CVE lands at correct tier
    Given a wiped DB
    When /test/scan is POSTed with [CVE-A, CVSS 9.5, nginx:1.24, kev=0, exposed=0]
    Then DB has one `open` row for CVE-A at tier=2
    And audit.log has one `finding_ingested` event

  Scenario: Scenario 02 — fix closes finding via BC-031 reconcile
    Given scenario 01's state (CVE-A open)
    When /test/scan is POSTed with an empty candidate list
    Then CVE-A transitions to `fixed_verified`
    And audit.log has an `auto_closed` event

  Scenario: Scenario 03 — regression fires 🔥 alarm
    Given scenario 02's state (CVE-A fixed_verified)
    When /test/scan is POSTed with [CVE-A] again
    Then CVE-A transitions to `regressed`
    And audit.log has a `regressed` event

  Scenario: Scenario 04 — snooze survives re-scan + excludes from first-fire
    Given a wiped DB
    And /test/scan is POSTed with [CVE-A]
    And status for CVE-A is set to `snoozed`
    When /test/scan is POSTed with [CVE-A, CVE-B]
    Then CVE-A remains `snoozed` and CVE-B first-fires as `open`

  Scenario: Scenario 05 — KEV CVE tiers at 0 regardless of CVSS
    Given a wiped DB
    When /test/scan is POSTed with [CVE-KEV, CVSS 7.5, kev=true]
    Then CVE-KEV lands at tier=0

  Scenario: Scenario 06 — internet-facing CVE tiers at 1
    Given a wiped DB
    When /test/scan is POSTed with [CVE-A, CVSS 9.5, source_ref=nginx:1.24, port_exposure={nginx:1.24: true}]
    Then CVE-A lands at tier=1 with internet_facing=1

  Scenario: Scenario 07 — digest content-hash bypasses interval
    Given /test/scan has fired 20 candidates, cap=10 → 1 digest sent
    When /test/scan fires same 20 again at `at = prev + 60` (within 24h)
    Then digest is throttled, audit has `digest_throttled`
    When /test/scan fires DIFFERENT 20 at `at = prev + 120`
    Then digest fires, audit has second `digest_sent`

  Scenario: Scenario 08 — heartbeat sweep reminds stale open findings
    Given CVE-A is `open` with `last_notified_ts = T0`
    When /test/sweep is POSTed at `at = T0 + 25h`
    Then audit has `heartbeat_reminder_sent` for CVE-A

  Scenario: Scenario 09 — multi-host isolation
    Given /test/scan POSTed with host=h1 [CVE-A], then host=h2 [CVE-A]
    Then DB has two separate `open` rows (one per host)
    And a snooze on h1's row does not affect h2's row

  Scenario: Scenario 10 — last_seen_ts bump on re-scan without new row
    Given CVE-A is `open` with `last_seen_ts = T0`
    When /test/scan re-POSTs the same [CVE-A] at `at = T0 + 300`
    Then no new DB row is created
    And CVE-A's `last_seen_ts` is updated to T0 + 300
```

## Capabilities delivered

- **CAP-T082: Live-stack scenario harness (Python runner + shell lifecycle)** — scenario class + ScenarioContext primitives + explicit scenario list + Makefile target.
- **CAP-T083: Hub test-mode endpoints (`POST /test/scan`, `/test/sweep`, `/test/wipe`) under `BEACONRY_TEST_MODE=1`** — env-gated at import time so routes are absent when the flag is off.
- **CAP-B022: Operator can verify pipeline behavior under simulated mutations without waiting on real grype / scout cadence / clock drift** — one command, deterministic.

Extends: CAP-T019 (scout scan loop — bypassed in scenarios), CAP-T071 (reconcile — verified by s02/s03), CAP-T079 (heartbeat — verified by s08), CAP-T080 (digest cadence — verified by s07).

## Links

- Design: `DESIGN.md § Core pipeline`
- Related: BC-017 (test-e2e — sibling gate, different scope), BC-031 (reconcile is the behavior under test here), BC-035/BC-036 (heartbeat + digest cadence verified via injected clock).
- Followup in IDEAS: extend `/ultrareview` to lint for exposed `/test/*` endpoints without the env gate.
