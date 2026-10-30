---
id: BC-016
title: Docs drift guard — Makefile help + README stay in sync with code
status: shipped
created: 2026-10-30
branch: feat/BC-016-docs-drift-guard
plan: docs/plan/BC-016-docs-drift-guard-plan.md
---

# BC-016: Docs drift guard

## Narrative

Operator ran `make help`, saw `STATUS=open|snoozed|accepted|fixed` for `make findings`, tried `make findings STATUS=fixed_verified` (the BC-011 state they just set) and found it worked anyway — the help was lying. Two drifts in one line: BC-011 added `fixed_verified` + `verification_failed`, BC-007's close-gap added the `BEACONRY_FINDINGS_LOCAL=1` operator override, neither made it into `make help`. The pattern recurs — `make help` already drifted once this session (targets `make status` / `make verify-dockerfile-health-copy` / `make verify-memory-drift` went missing after BC-012/013/015, caught at commit `94d1a45`). README has the same shape of enumerations and the same fragility. BC-016 adds `make verify-docs-drift` to the fast gate: a lint that cross-checks docs enumerations against code (STATUS values, make targets, BEACONRY_* env vars). Catches the recurrence class permanently; closes two live drifts as regression proof.

## Trigger

Operator ran `make` and found the findings help line didn't mention the two new BC-011 statuses or the BC-007 compose/local override.

## Urgency

N/A — meta / process safeguard.

## Expected outcome

- New `poc/scripts/verify_docs_drift.py` lint scans `Makefile`'s `help:` block and `README.md` for three enumeration classes and cross-checks them against code:
  - **Make targets** (`make <target>` strings in docs) → must exist in `Makefile`'s `.PHONY` list
  - **STATUS enum values** (`STATUS=foo|bar|...` strings in docs) → must match `poc/scripts/findings.py::STATUS_ORDER`
  - **BEACONRY_* env vars** mentioned in docs → must appear in at least one `os.environ.get("BEACONRY_X")` call in `poc/`
- Opposite direction: every `.PHONY` target must have at least one `make <target>` line in `Makefile`'s `help:` block (catches targets that go missing from help)
- Script exits 0 clean, 1 with named errors + copy-pasteable fix hints
- Hooked into `make check` fast tier via new `verify-docs-drift` target
- The two current drifts (make help findings STATUS enum + BEACONRY_FINDINGS_LOCAL mention) are fixed as regression proof — lint goes red before, green after
- Live-proven by Phase 4 planted-drift test (same pattern as BC-015)

## Out of scope

- Natural-language prose correctness — only structured enumerations
- Checking that env var *descriptions* match semantics — only presence
- Enforcing a specific ordering of listed values — only set membership
- `docs/` tree — covered by other reviews; this is specifically README + Makefile help, the two doc surfaces operators read first and that have shown drift
- `memory/` tree — covered by BC-015's memory drift guard

## Acceptance criteria (Gherkin)

```gherkin
Feature: Docs drift guard — make help + README stay in sync with code

  Scenario: Clean docs pass the gate
    Given Makefile help references only existing .PHONY targets, matching STATUS values, and real env vars
    When `make verify-docs-drift` runs
    Then exit code is 0

  Scenario: A STATUS value in help that doesn't exist in STATUS_ORDER is caught
    Given Makefile help lists "STATUS=open|snoozed|unicorn"
    And STATUS_ORDER in findings.py does not contain "unicorn"
    When the lint runs
    Then exit code is non-zero
    And stderr names the file, line, the stray value, and the valid STATUS_ORDER values

  Scenario: A make target in help that doesn't exist in .PHONY is caught
    Given Makefile help references `make not-a-real-target`
    When the lint runs
    Then exit code is non-zero
    And stderr names the missing target and the actual .PHONY entries

  Scenario: A .PHONY target not documented in help is caught
    Given Makefile .PHONY includes `undocumented-target` with no corresponding help line
    When the lint runs
    Then exit code is non-zero

  Scenario: A BEACONRY_* env var in README that no code reads is caught
    Given README mentions `BEACONRY_BOGUS` but no poc/*.py reads it
    When the lint runs
    Then exit code is non-zero

  Scenario: Regression proof — current drifts caught before fix
    Given the current make help lists STATUS=open|snoozed|accepted|fixed (missing BC-011's fixed_verified + verification_failed)
    When the lint runs on an un-updated Makefile
    Then it flags the missing values
    When the help text is updated to include all six STATUS values from STATUS_ORDER
    Then the lint passes
```

## Capabilities delivered

- **CAP-T053: `make verify-docs-drift` catches help/README enumerations that no longer match code** (technical, active) — new. Three enumeration classes (make targets, STATUS values, BEACONRY_* env vars) with bidirectional checks on make targets.

## Open questions

- (none — scope resolved by the "two drifts in two file surfaces" observation)

## Links

- Triggered by: operator-observed make help drift during BC-011 session
- Related: BC-015 (memory drift guard; same pattern class in `memory/`), CAP-T022 (fix/feat plan-doc enforcement; another cross-file consistency check)
