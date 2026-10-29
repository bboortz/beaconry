---
id: BC-NNN
title: <short title>
status: draft    # draft | ready | in-progress | shipped
created: YYYY-MM-DD
branch: feat/BC-NNN-<slug>          # once started
plan: docs/plan/BC-NNN-<slug>-plan.md    # once branch exists
---

# BC-NNN: <title>

## Narrative

One paragraph. Operator at desk, context, trigger, expected outcome.
Concrete narrative — "operator sees Telegram ping, taps Fix now, 90 seconds later
re-scan is clean" — not abstract.

## Trigger

Event, state, or operator action that activates this story.

## Expected outcome

What success looks like in operator-visible terms. Observable, not internal state.

## Out of scope

What this story explicitly does NOT cover. Prevents plan-doc scope creep.

## Acceptance criteria (Gherkin)

```gherkin
Feature: <feature summary>

  Scenario: <happy path name>
    Given <precondition>
    When <action>
    Then <observable outcome>
    And <optional secondary outcome>

  Scenario: <edge or failure path>
    Given <precondition>
    When <action>
    Then <outcome>
```

## Open questions

Captured during the 8-question clarification pass (see `_8-questions.md`).
Resolved by moving answers into the sections above.

## Links

- Design: `DESIGN.md § <section>` (if related)
- Related stories: BC-NNN
