---
id: BC-088
title: Finding lifecycle reference document
status: in-progress
created: 2026-11-05
branch: feat/BC-088-lifecycle-reference
plan: docs/plan/BC-088-lifecycle-reference-plan.md
---

# BC-088: Finding lifecycle reference document

## Narrative

Operator (or future contributor) wants to answer "what happens when a CVE
is matched?" — from first grype hit, through notification, operator button
tap, fix execution, re-scan verification, reconcile, auto-fix close,
regression. Today the answer is scattered across `memory/design-decisions.md`,
plan docs of ~15 BCs, and `poc/hub/hub.py` itself. Reading it takes an
afternoon per fresh question. BC-088 makes it a single page: every state,
every transition, every timer, every notification, every button —
documented once, in one place (`docs/LIFECYCLE.md`).

Secondary — and the real reason this is a BC, not a weekend docs chore —
the authoring exercise forces a comprehensive audit. Writing down every
state surfaces the gaps: `notify_auto_fix_regression`'s format helper is
dead code on the real Telegram path (code grep confirms, see plan doc
§5). The `[⏰ Snooze 4h]` button label is a lie. KEV refresh has been
403'ing hourly on hetzner for a day. The daily full rescan in
`memory/design-decisions.md` has no corresponding code. The lifecycle
doc inventories each category and feeds the next cycle of BCs with a
prioritised backlog.

## Trigger

Nothing event-driven. BC-088 is authored because the operator asked for
a single-page lifecycle reference after reading the BC-087 postmortem.

## Expected outcome

- `docs/LIFECYCLE.md` exists and covers: state diagram, ingest path,
  row timestamps, outbound notifications, operator buttons + handlers,
  fix pipeline, health waterfall, reconcile scope, isolation guarantees,
  timers/cadences, audit events, precedence at notify time.
- Five additional sections flag gaps: Unclearities, Necessary
  improvements, Known bugs, Missing implementation (vs
  design-decisions.md), Contradictions/inconsistencies.
- `docs/CAPABILITIES.md` has CAP-T182 pointing to the doc.
- `docs/stories/INDEX.md` has a BC-088 row.
- No code changes — fast tier (`make check`) stays green for this branch.

## Out of scope

- **Fixing any of the flaws the doc surfaces.** Each becomes a candidate
  future BC (BC-089 snooze timer, BC-090 KEV user-agent, BC-091
  auto-fix regression body wire-up, …). BC-088 is purely inventory.
- **Diagrams** beyond ASCII. Mermaid / PNG / SVG deferred — operators
  currently read the doc from the terminal or GitHub web view, both
  of which render ASCII fine.
- **Interactive navigation** (search, collapsible sections). Plain
  Markdown is the floor.

## Acceptance criteria (Gherkin)

```gherkin
Feature: finding lifecycle reference

  Scenario: Doc exists and lists every status
    Given docs/LIFECYCLE.md is present at HEAD
    When a reader greps the doc for each of the 9 status values
      (open, snoozed, accepted, fixed, fixed_verified, auto_fixed,
       regressed, regressed_from_auto_fix, verification_failed)
    Then every status appears at least once in a state-transition context

  Scenario: Doc lists every timer env var
    Given docs/LIFECYCLE.md is present
    When a reader greps the doc for each BEACONRY_* timer var that
      the lifecycle uses (SCAN_INTERVAL, BEACONRY_AUTO_FIX_THRESHOLD,
      BEACONRY_HEARTBEAT_*, BEACONRY_DIGEST_INTERVAL_S,
      BEACONRY_MAX_FINDINGS_PER_RUN)
    Then every var appears in the Timers section

  Scenario: Each "known-flaw" category has at least one entry
    Given the doc has sections for Unclearities, Necessary improvements,
      Known bugs, Missing implementation, Contradictions/inconsistencies
    Then none of those sections is empty — each lists at least one
      concrete entry with code-grep evidence or a design-decisions.md
      pointer

  Scenario: CAP-T182 exists and points to the doc
    Given docs/CAPABILITIES.md has a CAP-T182 entry
    Then its description names docs/LIFECYCLE.md verbatim
    And it is linked from the BC-088 plan's Shipping evidence table

  Scenario: Fast tier gates remain green
    Given the branch adds only Markdown files + CAPABILITIES/INDEX rows
    When `make check` runs
    Then ruff, pyright, pytest, coverage, verify-traceability,
      verify-docs-drift, verify-memory-drift, verify-evidence-pointers
      all pass with no new violations
```

## Capabilities delivered

- **CAP-T182: `docs/LIFECYCLE.md` — single source of truth for finding
  lifecycle** (type: technical, status: active)
  *Comprehensive reference: 12 descriptive sections + 5 known-flaw
  inventories. Referenced from CLAUDE.md as the authoritative lifecycle
  pointer. Future BCs that materially change a state transition,
  timer, or notification path MUST update LIFECYCLE.md as part of their
  Definition of Done.*

Extends: CAP-T084 (VARIABLES.md single-source-of-truth pattern).

## Open questions

Resolved by the "Unclearities" section of the doc itself — anything
that couldn't be answered by reading code + design-decisions.md becomes
a tracked unclearity for future resolution.

## Links

- `memory/design-decisions.md` — lifecycle rules locked 2026-10-29
- `docs/CAPABILITIES.md` — CAP-T176 through T181 are the most recent
  lifecycle-related caps
- `docs/plan/IDEAS.md` — pre-existing flaw backlog (snooze timer)
- PR #5 (BC-087) — surfaced the `notify_auto_fix_regression` body
  wire-up issue
