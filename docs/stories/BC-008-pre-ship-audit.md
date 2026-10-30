---
id: BC-008
title: Pre-ship audit protocol — Gherkin coverage, data-model symmetry, self-consistency
status: shipped
created: 2026-10-30
branch: feat/BC-008-pre-ship-audit
plan: docs/plan/BC-008-pre-ship-audit-plan.md
---

# BC-008: Pre-ship audit protocol

## Narrative

The developer finishes a feature branch, runs `make check-full`, sees green, and marks the story `shipped`. Today "green" doesn't mean "every Gherkin scenario has evidence" or "every write has a mirror read" or "this story's own conventions apply to its own artifacts". Across BC-002…BC-007, every single shipped story needed a follow-up `fix/` branch triggered by a post-ship audit the user kicked off. BC-008 makes that audit a required gate step inside the DD-TGD cycle so it fires *before* `shipped`, not after.

The mechanism is light: each plan doc grows a `Shipping evidence` table listing every Gherkin scenario + plan goal with its proof (test name, log line, "live-verified on …"). A plan can't be `shipped` until every row has evidence. If a row is `⚠️` or `❌`, the branch cycles back to in-progress.

## Trigger

Any time a story is near merge-readiness and the developer is tempted to flip status from `in-progress` to `shipped`.

## Expected outcome

- `docs/plan/_template.md` grows a `## Shipping evidence` section with a 4-column table (Scenario, Evidence type, Pointer, Status)
- `CLAUDE.md` DD-TGD gets a new step 6a: "Pre-ship audit"
- `memory/workflow.md` DD-TGD lists step 6a with the three audit lenses: Gherkin coverage, data-model symmetry, self-consistency
- `docs/stories/_8-questions.md` is unchanged (the audit happens at ship-time, not story-time)
- BC-003 draft (the still-open story) gets a preliminary Shipping evidence table showing which scenarios already have evidence vs which block shipping (0 live-tapped scenarios yet)
- README gets a short mention under the "Development process" section

## Out of scope

- Automated enforcement (verify-traceability parsing Gherkin → evidence table). This story ships Option A (template + process). Automation becomes a follow-up BC-NNN if the template-only version proves non-sticky.
- Retroactively filling the audit for BC-001..BC-007 (they already shipped + got audited + fixed via fix branches; past is sunk cost)
- Changing what "evidence" means (unit test / integration test / live verification / manual — all count; the point is the pointer field is populated)
- Rewriting the 9 clarification questions
- Introducing a reviewer agent

## Acceptance criteria (Gherkin)

```gherkin
Feature: Pre-ship audit protocol

  Scenario: Plan template includes Shipping evidence section
    Given docs/plan/_template.md
    When a developer opens it to start a new plan doc
    Then they find a "## Shipping evidence" section with a 4-column table
      (Scenario, Evidence type, Pointer, Status) and a usage hint

  Scenario: CLAUDE.md documents DD-TGD step 6a
    Given CLAUDE.md
    When grep -c "6a" is run
    Then step 6a "Pre-ship audit" appears in the Development process section
    And it names the three audit lenses

  Scenario: memory/workflow.md matches CLAUDE.md
    Given memory/workflow.md
    When the DD-TGD process section is read
    Then step 6a is present with the same three lenses

  Scenario: BC-003 draft has a Shipping evidence table
    Given docs/stories/BC-003-live-verified-buttons.md is still status=draft
    When the file is inspected
    Then a Shipping evidence table exists in the plan-doc section
      (or an explicit note that it will be filled when BC-003 moves to ready)

  Scenario: Verify-traceability recognises the new section
    Given a shipped BC-NNN plan doc with a Shipping evidence table
    When verify-traceability runs
    Then no new mismatch error is emitted for the new section
      (the current script doesn't parse it; this scenario just asserts
       no regression)

  Scenario: README names the audit protocol
    Given README.md
    When "## Development process" is read
    Then one bullet names the pre-ship audit step
```

## Capabilities delivered

- **CAP-B013: Developer trusts that `shipped` means every acceptance has evidence** (business, aspirational → active) — new.
- **CAP-T026: Plan template carries a Shipping evidence section used as the pre-ship checklist** (technical, active) — new.

Extends: CAP-B006 (clear development process) — the process now has an explicit audit step.

## Open questions

- (none — resolved during 2026-10-30 three-iteration challenge)

## Links

- Design: `memory/workflow.md § Development process`
- Prior: BC-002..BC-007 (every one of which had a post-ship fix branch that this protocol would have caught at the gate)
