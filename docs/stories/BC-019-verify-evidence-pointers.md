---
id: BC-019
title: Grep-verify every shipping-evidence pointer
status: shipped
created: 2026-10-30
branch: feat/BC-019-verify-evidence-pointers
plan: docs/plan/BC-019-verify-evidence-pointers-plan.md
---

# BC-019: Grep-verify every shipping-evidence pointer

## Narrative

The operator is Claude (and any developer running `make check` locally). Mental state: trusts the plan doc's `✓` marks implicitly as "this behavior is tested." BC-018's pre-ship audit filled a shipping-evidence table with 5 rows ✓ pointing at `test_telegram_mock.py` and two `test_hub_helpers.py` nodes — none of which existed. The behaviors were covered by the e2e test, but the pointers were vaporware. Caught only on the second-pass audit when the user asked "everything implemented?" This story moves that audit lens from vigilance to lint: every ✓ row in every plan's shipping-evidence table has its test-shaped pointer grep-verified against actual pytest node ids on every `make check`.

Urgency: N/A — dev tooling.

## Trigger

Any `make check` run. The lint re-runs every time anyone edits a `BC-*-plan.md` or renames/removes a test, so drift surfaces at commit time, not during a post-ship audit.

## Expected outcome

`make check` fails fast with a copy-pasteable list of dead pointers:

```
verify-evidence-pointers: 2 dead pointer(s):
  docs/plan/BC-018-telegram-mock-plan.md:
    row 2 ✓ pointer 'test_telegram_mock.py::test_injected_callback_delivered_via_getupdates'
      → file poc/tests/unit/test_telegram_mock.py: not found
    row 5 ✓ pointer 'test_hub_helpers.py::test_skip_grype_db_env_only'
      → file exists but pytest node id not collected
Fix: either implement the missing test or revise the plan row.
```

When all pointers resolve, the gate is silent and `make check` passes.

## Out of scope

- Does NOT verify that the test actually asserts the row's claim (semantic correctness is a human-review concern)
- Does NOT verify that every Gherkin Scenario has an evidence row (self-consistency lens — remains a manual pre-ship audit step)
- Does NOT replace `verify-traceability` — that's about CAPABILITIES ↔ tests; this is about plan tables ↔ tests
- Does NOT grep-verify non-test pointers (make targets, URLs, live-manual recipes, grep commands); only pointers containing `.py` and optional `::test_...` are strictly resolved

## Acceptance criteria (Gherkin)

```gherkin
Feature: Grep-verify shipping-evidence pointers at make-check time

  Scenario: dead file pointer fails the gate
    Given a plan with a ✓ row whose pointer is a .py file path that doesn't exist
    When make verify-evidence-pointers runs
    Then it exits non-zero
    And stderr names the plan, row index, and missing file path

  Scenario: dead node id pointer fails the gate
    Given a plan with a ✓ row whose pointer is file.py::test_x and file.py exists but test_x isn't a collected pytest node
    When make verify-evidence-pointers runs
    Then it exits non-zero
    And stderr names the plan, row index, and the uncollected node id

  Scenario: non-test-shaped pointer is skipped
    Given a plan with a ✓ row whose pointer is prose like "live manual", "grep diff", or a make target
    When make verify-evidence-pointers runs
    Then that row is skipped — only pointers matching the test-shape regex are strictly resolved

  Scenario: deferred rows marked ⚠️ or ❌ are not grep-verified
    Given a plan row with status column ⚠️ (deliberately deferred)
    When make verify-evidence-pointers runs
    Then that row is skipped — the lint only strictly validates ✓ rows

  Scenario: happy path — every pointer resolves
    Given every ✓ row across every BC-*-plan.md has a resolvable pointer
    When make check runs
    Then verify-evidence-pointers passes silently

  Scenario: new plan with no shipping-evidence section is grandfathered
    Given a BC-*-plan.md whose "## Shipping evidence" section is absent (not yet filled)
    When make verify-evidence-pointers runs
    Then that plan is skipped (consistent with verify-traceability's grandfathering rule)

  Scenario: parametrized test node ids are matched correctly
    Given a plan row pointer like test_x.py::test_fn[case-1]
    When make verify-evidence-pointers runs
    Then it matches the parametrized node id exactly, not the base test
```

## Capabilities delivered

- **CAP-T057: Evidence-pointer lint — every ✓ shipping-evidence row with a test-shaped pointer is grep-verified against actual pytest node ids** (type: technical, status: active)
  *Blocks `make check` when a plan's shipping-evidence table claims a test exists that doesn't. Closes the vigilance gap exposed by BC-018's two-pass audit.*

Depends on / extends:
- CAP-T017 (orphan tests fail the gate) — same shape of guard, different direction
- CAP-T022 (plan-doc-presence lint) — complementary; this story validates plan-doc *contents*

## Open questions

None. Scope and edge cases pinned by the 7 Gherkin scenarios above.

## Links

- Trigger: BC-018 close-gap audit (commit `b00d0aa`)
- Related: `docs/plan/BC-019-verify-evidence-pointers-plan.md`
- Related CAPs: CAP-T017, CAP-T022
