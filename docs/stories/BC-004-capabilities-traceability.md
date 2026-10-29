---
id: BC-004
title: Capabilities documentation + bidirectional test traceability
status: shipped
created: 2026-10-29
branch: feat/BC-004-capabilities-traceability
plan: docs/plan/BC-004-capabilities-traceability-plan.md
---

# BC-004: Capabilities documentation + bidirectional test traceability

## Narrative

A developer runs `make check`. A test fails. Right now the test name is cryptic (`test_make_id_differs_for_different_hosts`) and there's no obvious answer to "what does the system lose when this test is red?" They waste minutes grepping the codebase for context.

BC-004 fixes this: there's a single `docs/CAPABILITIES.md` listing everything the system guarantees — business capabilities (operator-facing: "receives a Telegram notification", "snoozes a finding") at the top, technical capabilities ("finding ID is stable across runs", "pipeline survives one failing notification") underneath. Each capability links to its origin story + design doc and to the tests that verify it. Every test file carries a `Covers:` docstring listing the capabilities it exercises, including human names so the developer doesn't need to jump to the index just to know what matters.

A `make verify-traceability` script runs as part of `make check` and fails the gate on any dangling link: orphan tests, unverified capabilities, missing files on either side. The matrix can't rot silently.

## Trigger

Any of these: (a) a test fails and someone wants to know what capability is at risk; (b) a developer plans a new feature and wants to see what capabilities already exist; (c) a key user asks "what does beaconry guarantee for me?"

## Expected outcome

- `docs/CAPABILITIES.md` exists, seeded with ~5 business and ~15 technical capabilities derived from BC-000, BC-001, BC-002
- Each capability has: id, type, description, origin (story + design), supports (upward link), verified by (test file list), status
- Flat `CAP-NNN` ID space; type encoded as a field (`type: business` or `type: technical`), rendered in two sections for scannability
- Every existing test module has a top-of-file `Covers:` docstring block naming the capabilities (both CAP id and human name)
- `make verify-traceability` runs on every `make check` and fails CI on:
  - test with no `Covers:` block
  - test's `Covers:` references a non-existent CAP
  - CAP's `Verified by:` references a non-existent test file
  - active CAP with zero verifying tests
- CLAUDE.md + memory/workflow.md updated: DD-TGD gets step "5a: Register capabilities"; the 8 canonical clarification questions become 9 (new: "What capabilities does this story deliver or extend?")

## Out of scope

- UI / web browser for capabilities
- Auto-generation of CAPABILITIES.md from source code
- Linking capabilities to production code (just tests for BC-004)
- Capability versioning / semver
- Function-level test linking (file-level is enough for now)
- Retroactively updating BC-003's draft to include "What capabilities" answer — that happens when BC-003 moves `draft → ready`

## Acceptance criteria (Gherkin)

```gherkin
Feature: Capabilities documentation + bidirectional traceability

  Scenario: CAPABILITIES.md is seeded with business and technical capabilities
    Given the project at HEAD after BC-004
    When "cat docs/CAPABILITIES.md" is run
    Then it contains a "## Business capabilities" section with at least 5 entries
    And a "## Technical capabilities" section with at least 15 entries
    And every capability has: id (CAP-NNN), type, origin story, description,
        verified_by, status

  Scenario: Every test file carries a Covers block
    Given the project at HEAD
    When "pytest --collect-only" is run
    Then every collected test module has a top-of-file docstring containing
        a "Covers:" block with at least one CAP id

  Scenario: verify-traceability fails on orphan test
    Given a new test file poc/tests/unit/test_orphan.py with no Covers block
    When "make verify-traceability" is run
    Then exit code is non-zero
    And stderr names "poc/tests/unit/test_orphan.py" and "missing Covers block"

  Scenario: verify-traceability fails on dangling CAP reference
    Given a test file lists "CAP-X999" in Covers but CAPABILITIES.md has no CAP-X999
    When "make verify-traceability" is run
    Then exit code is non-zero
    And stderr names the dangling CAP id and the referring test file

  Scenario: verify-traceability fails on unverified active CAP
    Given a CAP in CAPABILITIES.md has status=active and no Verified by entries
    When "make verify-traceability" is run
    Then exit code is non-zero
    And stderr names the unverified CAP id

  Scenario: verify-traceability fails on missing test file
    Given a CAP's Verified by lists a test file path that doesn't exist on disk
    When "make verify-traceability" is run
    Then exit code is non-zero
    And stderr names the missing path

  Scenario: verify-traceability is part of make check
    When "make check" is run on a clean HEAD
    Then it invokes verify-traceability
    And the gate passes

  Scenario: Workflow docs updated
    Given CLAUDE.md and memory/workflow.md at HEAD
    When grep-ed for "capabilities"
    Then CLAUDE.md documents step 5a "Register capabilities" in DD-TGD
    And memory/workflow.md's 8-question list becomes 9 including the new
        "What capabilities does this story deliver or extend?"

  Scenario: Orphan-test detection is loud but recoverable
    Given a developer adds a test file without Covers (common mistake)
    When they run make check
    Then they see a helpful error naming the file and a one-line suggestion
        template they can paste
```

## Open questions

- (none — resolved during 2026-10-29 clarification + 3 virtual-user iterations)

## Links

- All prior stories (BC-000 → BC-003) retroactively map into the initial seed
- Design: DESIGN.md (whole document — this story operationalizes DESIGN against tests)
- Related: BC-003 (draft; its live-verify scenarios will become capabilities too once it ships)
