---
id: BC-147
title: docs/ three-folder layout — specs / plan / stories
status: in-progress
created: 2026-11-21
branch: feat/BC-147-docs-specs-layout
plan: docs/plan/BC-147-docs-specs-layout-plan.md
---

# BC-147: docs/ three-folder layout — specs / plan / stories

## Narrative

Future-me (or a new contributor) opens `docs/` and must guess what each
top-level file is for: is `CAPABILITIES.md` a spec, a report, a plan? Is
`DESIGN.md` at repo-root authoritative or legacy? The current layout is
accidental — every doc that wasn't a plan or a story ended up at the top of
`docs/` or at the repo root. This story promotes the implicit three-class
taxonomy already in Claude's head (spec / plan / story) into first-class
directories, moves the six existing specification docs into `docs/specs/`,
and gates the layout with a lint so the next doc lands in the right place.

## Trigger

Operator reads `CLAUDE.md` + wanders into `docs/`, sees inconsistent
placement, asks "where do I put the next spec?" There is no answer today.
Story also fires when `make check` would accept a new `.md` under
`docs/` top-level that should have gone under `docs/specs/`.

## Expected outcome

- `docs/specs/` exists and holds every spec-shaped doc:
  `CAPABILITIES.md`, `LIFECYCLE.md`, `METRICS.md`, `VARIABLES.md`,
  `INSTALL.md`, `DESIGN.md` (moved from repo root).
- `docs/specs/_template.md` + `docs/specs/INDEX.md` mirror the stories
  scaffold.
- `docs/plan/` and `docs/stories/` unchanged in layout.
- `docs/operations/` unchanged (operations runbooks are a distinct class).
- Every code reference to the moved files (scripts, tests, Makefile,
  memory, CLAUDE.md, in-repo cross-links) resolves post-move.
- A new lint (`verify-docs-layout`) refuses any `.md` at `docs/` top-level
  other than `README.md`; and refuses any new spec placed outside
  `docs/specs/`.

## Out of scope

- Rewriting the content of any moved doc. This is a mechanical move +
  reference refresh.
- Reclassifying existing plans or stories. Those directories are not
  touched.
- Changing CAPABILITIES.md's internal structure (the CAP-NNN registry
  stays exactly as it is).

## Acceptance criteria (Gherkin)

```gherkin
Feature: docs three-folder layout

  Scenario: Specs live under docs/specs/
    Given the beaconry repo at HEAD
    When I list `docs/specs/*.md`
    Then it contains CAPABILITIES.md, LIFECYCLE.md, METRICS.md,
      VARIABLES.md, INSTALL.md, DESIGN.md, _template.md, INDEX.md

  Scenario: No stray specs at docs/ top-level
    Given the beaconry repo at HEAD
    When I list `docs/*.md` (non-recursive)
    Then only README.md (if present) remains at top-level

  Scenario: DESIGN.md moved from repo root
    Given the beaconry repo at HEAD
    When I ls / for DESIGN.md
    Then it is absent; DESIGN.md lives under docs/specs/

  Scenario: Every in-repo reference to a moved file resolves
    Given every .md, .py, Makefile, and shell script in the repo
    When I grep for links or path strings naming any moved doc
    Then every reference resolves to an extant file after the move

  Scenario: Layout lint blocks regression
    Given the branch tip
    When a developer adds a .md to docs/ top-level (other than README.md)
    Then `make check` fails with a message pointing at docs/specs/

  Scenario: Memory + CLAUDE.md names the three folders
    Given memory/workflow.md + CLAUDE.md
    When I grep for "docs/specs"
    Then both files mention the new folder in their layout section
```

## Capabilities delivered

- **CAP-D001: docs/ three-folder layout (specs / plan / stories)**
  (type: technical, status: active)
  *Every doc under docs/ belongs to exactly one of three classes with
  a dedicated subdirectory. `docs/operations/` is a separate class for
  runbooks. `verify-docs-layout` lints the invariant on every
  `make check`.*

## Open questions

Resolved in the 4-question clarification pass (2026-11-21):
- Q1: All 5 top-level `docs/*.md` move → `docs/specs/`. [answered]
- Q2: `DESIGN.md` → `docs/specs/`; `docs/operations/` stays. [answered]
- Q3: Full DD-TGD process (feat/BC-NNN). [answered]
- Q4: Add `_template.md` + `INDEX.md` for specs/. [answered]

## Links

- Design: DESIGN.md § Repository layout (this story moves DESIGN.md itself)
- Related stories: BC-016 (docs-drift guard — complementary)
