---
id: BC-006
title: Tighten the shipping gate — fast vs full tiers
status: shipped
created: 2026-10-30
branch: feat/BC-006-tighten-gate
plan: docs/plan/BC-006-tighten-gate-plan.md
---

# BC-006: Tighten the shipping gate — fast vs full tiers

## Narrative

The operator runs `make check` on their fix branch, sees green, pushes. Today "green" means lint + type + pytest + traceability pass — but not that `make smoke` works. BC-005's smoke regression lived 24 hours precisely because the gate was lying. BC-006 reshapes the gates into two tiers:

- `make check` — **fast tier**, ~5 seconds. Lint + type + pytest + coverage + traceability + smoke. The pre-push hook + the local inner loop + the mandatory pre-merge gate.
- `make check-full` — **full tier**, ~1–2 minutes. Fast tier + opt-in integration tests (`compose`, `real_tools` markers). What CI will run when CI exists.

README + CLAUDE.md + `memory/workflow.md` document the tier table (what each gate catches, approximate cost, when to run).

## Trigger

- A developer finishes a feature and wants to know whether to push
- A reviewer wants to know what "ran successfully" actually covered
- Future CI job needs an entry point

## Expected outcome

- `make check` includes `smoke` as a dependency; broken smoke mode fails the gate
- `make check-full` runs `make check` and then invokes `pytest -m "compose or real_tools"` (overriding the default `-m "not real_tools and not compose"` addopts)
- README has a "Gate tiers" table naming each gate, its cost, and when to run
- CLAUDE.md DD-TGD step 6 names `make check-full` as the merge-ready gate; `make check` as the pre-push gate
- `memory/workflow.md` gate matrix shows tier for every row
- New capabilities registered: CAP-T023 (`make check` includes smoke), CAP-T024 (`make check-full` includes opt-in markers), CAP-B011 (operator-facing explanation of tiers)

## Out of scope

- CI pipeline itself (still deferred until remote exists; this story just creates the right entry point)
- Automated compose-up/down live test (deferred — needs docker runner in CI)
- Dependency / supply-chain audit (`pip-audit`, bandit) — separate story
- Any new unit or integration test content; just restructuring invocation
- Changing which checks are mandatory vs advisory (BC-002's locked set stands)

## Acceptance criteria (Gherkin)

```gherkin
Feature: Fast / full gate tiers

  Scenario: `make check` includes smoke
    Given smoke is broken (e.g. BEACONRY_MOCK=1 python -m poc.demo exits non-zero)
    When `make check` is run
    Then the gate fails with a message naming smoke

  Scenario: `make check` is fast
    When the operator times `make check` with a cold venv but prebuilt deps
    Then wall-clock runtime is below 15 seconds on a typical dev laptop
      (gate stays usable in the pre-commit / pre-push loop)

  Scenario: `make check-full` includes opt-in markers
    When `make check-full` is run on a dev with docker compose available
    Then `pytest -m "compose or real_tools"` runs and the marker-opt-in tests execute
    And the result includes both the fast-tier result and the opt-in tier result

  Scenario: `make check-full` skips markers gracefully when deps missing
    Given docker compose is not available on the host
    When `make check-full` is run
    Then the compose-marker tests are skipped (not failed)
    And the gate exits 0 if everything else passed

  Scenario: Gate tier table is documented
    Given README.md and CLAUDE.md
    When a new developer reads them
    Then both name `make check` as the fast tier (pre-push, inner loop)
    And both name `make check-full` as the full tier (CI, pre-merge sanity)

  Scenario: Workflow memory names the tier for each gate row
    Given memory/workflow.md
    When the "Blocking gates" matrix is inspected
    Then each row carries a column or label naming whether it runs in fast or full tier
```

## Capabilities delivered

- **CAP-B011: Operator trusts the gate — green `make check` means "smoke-ready", green `make check-full` means "CI-ready"** (business, aspirational → active) — new.
- **CAP-T023: `make check` includes smoke so pipeline regressions are caught at the gate** (technical, active) — new.
- **CAP-T024: `make check-full` runs opt-in markers (compose, real_tools) in addition to the fast tier** (technical, active) — new.

Extends: CAP-B006 (clear development process) now carries the tier explanation.

## Open questions

- (none — resolved during 2026-10-30 clarification + 3 virtual-user iterations)

## Links

- Design: `memory/workflow.md`
- Prior: BC-005 smoke regression that motivated this story (`docs/plan/IDEAS.md § Process lessons learned`)
