# Canonical story clarification — 8 questions

When a rough operator scenario arrives, Claude asks these 8 questions to produce an actionable, Gherkin-ready story file. Each answer maps to a specific section of `_template.md`.

1. **Who** is the operator in this moment?
   *Role, mental state, what else they are doing.* → **Narrative**

2. **When** does the story fire?
   *Trigger — event, state change, or cadence.* → **Trigger**

3. **What** does success look like, concretely?
   *Observable in operator-visible terms, not internal state.* → **Expected outcome**

4. **How urgent?**
   *Tier 0 / 1 / 2 / 3 from `design-decisions.md`, or N/A.* → **Narrative** (as colour)

5. **Happy path** — exact sequence of operator-visible steps?
   *Numbered list, operator action → system response.* → **Scenario: happy path**

6. **Edge cases / failure modes** that matter?
   *What can go wrong, how we handle it.* → **Scenario: edge / failure**

7. **Out of scope** — what could creep in but shouldn't?
   *Explicit non-goals.* → **Out of scope**

8. **How do we know it's done?**
   *Acceptance test wording — becomes Gherkin.* → **Acceptance criteria (Gherkin)**

## Exit criteria for the clarification pass

- All 8 questions answered or explicitly declined ("N/A because…").
- Gherkin block compiles mentally — each `Then` is observable without opening a shell.
- Status flipped from `draft` to `ready`.
- Entry added or updated in `INDEX.md`.

## When the operator provides multiple stories at once

Ask the 8 questions **per story**, not once for the batch. Related stories still get separate files; link them via `## Links`.
