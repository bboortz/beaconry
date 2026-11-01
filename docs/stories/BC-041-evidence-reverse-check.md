---
id: BC-041
title: verify-evidence-pointers reverse check — every test referenced by a plan row
status: shipped
created: 2026-11-01
branch: feat/BC-041-evidence-reverse-check
plan: docs/plan/BC-041-evidence-reverse-check-plan.md
---

# BC-041: verify-evidence-pointers reverse check

## Narrative

BC-019 shipped `verify-evidence-pointers`: every ✓ row in a `BC-NNN-plan.md`'s shipping-evidence table must resolve to a real pytest node. The policy is one-directional — **pointer → test**. The reverse direction (**test → pointer**) isn't checked, which created a quiet class of audit misses in BC-031 and BC-040: tests that shipped as part of a close-gap landed in the test file without a corresponding plan row. Nothing failed; nothing in the pre-ship audit caught it. The developer who runs the audit sees `all green` and ships.

Concrete example: BC-031's close-gap landed three backfill tests. BC-019's lint saw zero mismatches because the policy is "resolve each row to a test," not "every test has a row". The three tests were real, worked correctly, and were orphans from the plan's perspective — invisible to downstream review.

This story adds a BC-024-style reverse pass: for every `test_*` function in `poc/tests/integration/test_BC_NNN_*.py`, the matching `docs/plan/BC-NNN-*-plan.md` must contain at least one shipping-evidence row pointing at it.

## Trigger

Any PR that adds a new `test_*` function to an integration test file whose name encodes a BC number.

## Expected outcome

- `make check` fails if an integration test exists without a plan row pointing at it.
- Error message names the orphan test + the plan it should be in + a copy-pasteable row template.
- Explicit allowlist for intentional meta-tests or helpers (e.g. shared fixture-only functions, cross-BC smoke tests).
- No change to the forward direction (pointer → test) — BC-019's logic stays.

## Out of scope

- Reverse check for unit tests. Unit tests often cover shared helpers used by multiple BCs; they wouldn't map 1:1 to a plan.
- Checking that the row's description matches the test's docstring. Low signal, high churn.
- Enforcing that every scenario in the Gherkin block has a dedicated test (that's BC-008's audit protocol territory).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Reverse evidence check

  Scenario: Integration test referenced by plan row → pass
    Given `test_BC_041_demo.py::test_x` exists
    And `BC-041-demo-plan.md` has a shipping-evidence row with pointer `test_BC_041_demo.py::test_x`
    When `make verify-evidence-pointers` runs
    Then exit 0

  Scenario: Integration test NOT referenced by any row → fail
    Given `test_BC_041_demo.py::test_orphan` exists
    And `BC-041-demo-plan.md` has no row pointing at it
    When `make verify-evidence-pointers` runs
    Then exit non-zero
    And output names `test_orphan` + the plan file + a copy-pasteable row template

  Scenario: Allowlisted helper test → pass
    Given `test_BC_041_demo.py::_helper_fixture_check` is in the allowlist
    When `make verify-evidence-pointers` runs
    Then no violation

  Scenario: Integration test for an unshipped / draft BC → soft-pass
    Given `test_BC_999_wip.py` exists
    And no `BC-999-*-plan.md` is present
    When `make verify-evidence-pointers` runs
    Then no reverse-check violation for that file (BC-019's existing lint already handles the forward direction)

  Scenario: Pointer uses the file-less `::node` form → still matches
    Given `BC-041-demo-plan.md` has a row with pointer `test_BC_041_demo.py::test_x`
    When reverse-check runs
    Then `test_x` is considered referenced

  Scenario: `make check` enforces the gate
    Given a violation introduced
    When operator runs `make check`
    Then verify-evidence-pointers step fails and halts the fast tier
```

## Capabilities delivered

- **CAP-T088: `verify-evidence-pointers` reverse pass — every integration test node is referenced by a plan row** (technical, active) — same gate, two directions; mirrors BC-024's traceability reverse direction.
- **CAP-B025: Pre-ship audit catches partial close-gap fixes** (business, active) — if a developer adds a test but forgets the plan row, CI fails. Pre-BC-041 this was a quiet class of audit miss.

Extends: CAP-T017 (BC-004 traceability base), CAP-T054 (BC-019 forward pointer check), CAP-T062 (BC-024 reverse traceability — same pattern, different axis).

## Links

- Design: `memory/code-quality.md`
- Related: BC-019 (forward direction), BC-024 (reverse traceability pattern).
