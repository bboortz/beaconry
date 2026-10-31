---
id: BC-021
title: Lint — every service-local .py is COPY'd by its Dockerfile
status: shipped
created: 2026-10-31
branch: feat/BC-021-dockerfile-copy-lint
plan: docs/plan/BC-021-dockerfile-copy-lint-plan.md
---

# BC-021: Lint — every service-local .py is COPY'd by its Dockerfile

## Narrative

Operator is Claude (or any contributor) adding a new module to `poc/hub/` and running `make check` before pushing. BC-020 shipped `poc/hub/kev.py` + used `poc/hub/urgency.py` as a new import; both were missing from the hub Dockerfile's `COPY hub/hub.py hub/waterfall.py ./` line. `make check` was green because unit tests use the host path (`poc.hub.kev`); the hub container died at boot with `ModuleNotFoundError`. Only `make test-e2e` surfaced it — a full container rebuild late in the pipeline. BC-012 added `verify-dockerfile-health-copy` to catch exactly this class of regression for `beaconry_health/`; BC-021 generalizes the same pattern to intra-service .py files.

Urgency: N/A — dev tooling.

## Trigger

Any `make check` run. The lint re-runs every time anyone adds / renames / removes a .py file in `poc/<service>/`.

## Expected outcome

`make check` fails fast with a copy-pasteable diff hint:

```
verify-dockerfile-sources: found issues:
  poc/hub/Dockerfile: Python files exist in poc/hub/ but Dockerfile does not COPY them:
    - kev.py
    - urgency.py
  Fix: add them to the `COPY hub/*.py ./` line (or use a glob).
```

Clean pass is silent.

## Out of scope

- Not checking that COPY lines point at files that exist (reverse direction — stale COPYs are easy to spot in `docker build`).
- Not checking nested package dirs (handled by directory-level COPY like `COPY hub/executor/ executor/`).
- Not auto-fixing. Lint fails; dev fixes.
- Not checking test files or `__init__.py` (they're not entrypoint code).
- Not expanding to a general "entrypoint-import-graph" check — overkill; "every .py must be COPY'd" is the simpler policy with the same regression coverage.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Dockerfile ships every service-local .py

  Scenario: service with uncovered .py fails the lint
    Given a service dir with foo.py and bar.py
    And its Dockerfile only COPYs foo.py
    When make verify-dockerfile-sources runs
    Then it exits non-zero
    And stderr names Dockerfile, service dir, and the uncovered file bar.py

  Scenario: service with all .py files covered passes
    Given a service dir with foo.py and bar.py
    And its Dockerfile COPYs both (any syntax: individual, glob, or dir)
    When make verify-dockerfile-sources runs
    Then it exits 0 silently

  Scenario: __init__.py and test_*.py are not required to be COPY'd
    Given a service dir with service.py, __init__.py, test_fixture.py
    And its Dockerfile only COPYs service.py
    When make verify-dockerfile-sources runs
    Then it passes

  Scenario: service dir without a Dockerfile is skipped
    Given poc/beaconry_health/ (shared package, no Dockerfile)
    When make verify-dockerfile-sources runs
    Then it does not fail

  Scenario: glob COPY matches all .py in the directory
    Given a Dockerfile with COPY service/*.py ./
    And a service dir with foo.py and bar.py
    When make verify-dockerfile-sources runs
    Then it passes (the glob covers both)

  Scenario: Retroactive run against the real repo (hub + scout + telegram_mock) is clean
    When make verify-dockerfile-sources runs against poc/
    Then it exits 0 silently
```

## Capabilities delivered

- **CAP-T061: Dockerfile-sources lint — every service-local .py is COPY'd by its Dockerfile** (type: technical, status: active)
  *Prevents BC-020-class ship-time packaging regressions where a new import lands in a service but the Dockerfile's explicit COPY list isn't updated.*

Depends on / extends:
- CAP-T019 (BC-012's `verify-dockerfile-health-copy`) — same class of guard; different direction (external shared-package COPY vs intra-service COPY).

## Open questions

None. Policy pinned by the 6 Gherkin scenarios.

## Links

- Trigger: BC-020 live-smoke (commit `d855ddc`)
- Related: BC-012 (prior art for Dockerfile-COPY lints), BC-019 (same "move vigilance to lint" ethos)
