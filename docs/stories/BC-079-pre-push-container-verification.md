---
id: BC-079
title: Pre-push runs full container verification
status: shipped
created: 2026-11-03
branch: feat/BC-079-pre-push-container-verification
plan: docs/plan/BC-079-pre-push-container-verification-plan.md
---

# BC-079: Pre-push runs full container verification

BC-078 landed distroless. The post-ship verification caught two
pre-existing holes:

1. `test_hub_container_has_no_test_mode_code` has been failing
   silently since BC-038 (2026-10-31) because BC-038 re-introduced
   the `BEACONRY_TEST_MODE` string into `hub.py` for the scenario
   harness. The test was a source-string scan — a weak proxy for
   the real BC-018 contract (test routes unreachable in prod).
2. `Makefile:268` prefixes the pytest invocation with `-$(PYTHON)`,
   making Make swallow test-e2e failures. `make check-full` printed
   `✓ passed` while test-e2e was red.

Combined, (1) + (2) means a shipped-and-staged story could silently
regress the container boot flow without any gate flagging it.

## Operator / developer scenario

**Who**: developer about to `git push`.

**When**: pre-push hook fires. Currently runs pytest (unit + integ)
+ linters + verify-\* checks. Missing: container-level verification
(compose integration, test-e2e, test-scenarios, test-cli).

**Success**: pre-push boots a throwaway hub+scout stack, verifies
all four endpoints, verifies CVE pipeline end-to-end, verifies
scenario harness. Push blocks on any failure. Container regressions
caught before they reach `dev`.

**Urgency**: Tier 2 — pre-emptive. BC-078 landed shipshape; this
closes the "could-have-silently-regressed" gap.

## Gherkin

```
Feature: pre-push container verification

Scenario: test-e2e failure blocks the push
  Given a developer tries to git push with a broken hub.py
  When the pre-push hook runs
  Then make check-full fires
   And test-e2e builds + boots the test stack
   And a failing e2e test propagates its exit code up through Make
   And the push is rejected with a non-zero exit

Scenario: the stale BEACONRY_TEST_MODE check is replaced with a runtime probe
  Given the shipped hub.py contains the string BEACONRY_TEST_MODE
   (legitimate, because BC-038 scenario harness gates /test/* routes on it)
  When test_hub_container_has_no_test_mode_code runs
  Then the test probes http://hub-test/test/__driver__/<anything> at runtime
   And asserts the response is 404 because BEACONRY_TEST_MODE is unset
   And no longer scans the source for a string that is now a legitimate feature flag

Scenario: pre-push skips live-stack CLI smoke
  Given the developer may not have the operator stack running
  When the pre-push hook invokes make check-full
  Then BEACONRY_SKIP_CLI_SMOKE=1 is set in the hook env
   And test-cli skips cleanly (prints "cli-smoke: skipped" and exits 0)
```

## Capabilities

- **CAP-T162** (new): Pre-push hook invokes `make check-full` with
  CLI-smoke skipped. Blocks push on container regressions.
- **CAP-T163** (new): `test_hub_container_has_no_test_mode_code`
  rewritten as a runtime probe — `GET /test/__driver__/*` returns 404.

## Out of scope

- Running the real `make live-rebuild` + probing `/healthz` of the
  operator's actual stack during pre-push. test-e2e already gives
  equivalent coverage via an isolated throwaway stack, so adding
  the live stack probe only adds flakes (what if the operator
  isn't running the stack?).
- CI-side mirror of this hook — ci.yml already runs `make check-full`.

## Not addressed (future stories)

- Pre-push caching: skip the make check-full step if nothing under
  `poc/` or `pyproject.toml` changed since the last successful run.
