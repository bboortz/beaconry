---
id: BC-024
title: verify-traceability enforces reverse direction (CAP → test's Covers block)
status: shipped
created: 2026-10-31
branch: feat/BC-024-traceability-reverse-check
plan: docs/plan/BC-024-traceability-reverse-check-plan.md
---

# BC-024: verify-traceability enforces reverse direction

## Narrative

Operator is Claude (or any contributor) shipping a new story. The pattern of adding a CAP to `CAPABILITIES.md` with `verified by: - test_x.py`, then forgetting to add the CAP id to `test_x.py`'s `Covers:` block — has silently landed in **four consecutive stories** (BC-018, BC-020, BC-022, BC-023). Each three-lens audit catches it manually, each time it's logged, each time the next story re-introduces it. The existing `verify-traceability` lint already enforces the forward direction (test claims a CAP → CAP must list test); it just doesn't enforce the reverse. BC-024 adds the reverse so the drift is caught at `make check` time instead of in a post-ship audit.

Urgency: N/A — dev tooling.

## Trigger

Any `make check` run. Fires whenever a CAP's `verified by:` list contains a test file whose `Covers:` block doesn't claim that CAP id.

## Expected outcome

```
verify-traceability: found mismatches:

CAPABILITIES.md § CAP-T063 lists poc/tests/unit/test_hub_helpers.py as verified-by,
but that file's Covers: block doesn't claim CAP-T063. Add '- CAP-T063: ...' to
the Covers block, or remove the file from verified-by.

1 mismatch(es).
```

Clean pass is silent.

## Out of scope

- Not auto-adding the missing CAP to the Covers block (fail → human fixes).
- Not relaxing the forward check — both directions stay strict.
- Not fuzzy-matching Covers block entries. CAP id matches exactly (CAP-T063 in Covers vs CAP-T063 in verified-by).
- Not retroactively migrating historic CAPs — any drift surfaced on first run gets fixed in this branch.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Reverse-direction traceability check

  Scenario: CAP lists a test, test's Covers block omits the CAP → fail
    Given CAPABILITIES.md § CAP-X has `verified by: - test_x.py`
    And test_x.py's Covers: block does NOT include CAP-X
    When make verify-traceability runs
    Then it exits non-zero
    And stderr names CAP-X, the test file path, and a copy-pasteable fix hint

  Scenario: Both directions covered → pass
    Given CAPABILITIES.md § CAP-X has verified-by test_x.py
    And test_x.py's Covers: block includes CAP-X
    When make verify-traceability runs
    Then it exits 0 silently

  Scenario: CAP lists a test file that doesn't exist (unchanged)
    Given CAP-X has verified-by non_existent.py
    When make verify-traceability runs
    Then it emits the pre-existing file-missing error (not the new reverse-direction error)
    And no double-report for the same CAP/test pair

  Scenario: Deprecated CAP with no verified-by is exempt (unchanged)
    Given CAP-Y has status=deprecated and empty verified-by
    When make verify-traceability runs
    Then it does not fire the reverse check for CAP-Y

  Scenario: Retroactive run against real repo clean
    When make verify-traceability runs against the current repo (post-BC-023 close-gap)
    Then it exits 0 silently
```

## Capabilities delivered

- **CAP-T064: verify-traceability enforces reverse direction — CAP → test's Covers block** (type: technical, status: active)
  *Catches the pattern where a CAP's verified-by list a test file whose Covers: block omits the CAP id. BC-018/020/022/023 all shipped with this drift and had to be audit-fixed manually. BC-024 moves the catch to make check time.*

Depends on / extends:
- CAP-T017, CAP-T022 — same lint, forward direction already enforced; this adds the reverse.

## Open questions

None. Policy pinned by the 5 Gherkin scenarios.

## Links

- Pattern this closes: BC-018, BC-020, BC-022, BC-023 all needed post-ship Covers-block fixes.
- Related: BC-019 (same "move vigilance to lint" ethos; evidence-pointer resolution).
