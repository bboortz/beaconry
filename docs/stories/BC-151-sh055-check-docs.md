---
id: BC-151
title: check-docs fleet-tier wrapper (SH-055 mirror)
status: shipped
created: 2026-11-23
branch: feat/SH-055-check-docs
plan: docs/plan/BC-151-sh055-check-docs-plan.md
---

# BC-151 — `check-docs` fleet-tier wrapper

## Narrative

Agent or human about to push a beaconry change. They've updated code and
docs, but may have missed a stale `INDEX.md` row, forgotten to bump VERSION
across the three sync points (BC-062), or left an evidence pointer pointing
at a moved test. They run `make check-docs` (or push, firing `hook-push`),
and one of the six existing drift gates catches the staleness before it
reaches `origin/dev`.

## Trigger

- Developer runs `make check-docs` directly, or
- `git push` fires the pre-push hook → `make hook-push` → `PUSH_STEPS`
  includes `check-docs`, or
- PR opened → GitHub Actions runs `make ci-branch` → `BRANCH_STEPS`
  includes `check-docs`.

## Expected outcome

- `make check-docs` exits 0 on a clean tree and non-zero when any of the
  six delegates reports drift.
- The same `make check-docs` invocation works on every fleet repo; the
  wrapper name is fleet-aligned (shepherd / healthy / HRC / homelab /
  kernel-test / forge) while each repo keeps its native delegate names.

## Out of scope

- Inventing new drift checks. The wrapper is a pure delegate; beaconry
  already had each of the six verify-\* targets.
- Renaming beaconry's drift targets. Same rejection as SH-042: fleet
  alignment is on the *wrapper* name, not the step names.
- Running `check-docs` from `hook-commit` (~15 s budget — too tight for
  the full six-chain run). Pre-push + CI-on-PR is the right stage.

## Acceptance criteria (Gherkin)

```gherkin
Feature: fleet-wide check-docs wrapper (shepherd SH-055 mirror)

  Scenario: check-docs target exists
    Given beaconry's Makefile
    When  `grep '^check-docs:' Makefile` runs
    Then  the target is declared
    And   .PHONY: check-docs is declared

  Scenario: check-docs runs the six delegate targets
    Given beaconry's check-docs definition
    Then  verify-docs-drift, verify-docs-layout, verify-version-sync,
          verify-evidence-pointers, verify-traceability, verify-install-doc
          are its prerequisites

  Scenario: PUSH_STEPS and BRANCH_STEPS include check-docs
    Given beaconry's Makefile
    Then  PUSH_STEPS contains "check-docs"
    And   BRANCH_STEPS contains "check-docs"
    And   `make -n hook-push` emits the check-docs chain
    And   `make -n ci-branch` emits the check-docs chain
```

## Capabilities delivered

- **CAP-S009: Fleet-tier check-docs wrapper (shepherd SH-055)** (type:
  technical, status: active) — see `docs/specs/CAPABILITIES.md`.

Extends: `CAP-S008` (hook-commit/hook-push wrappers) by giving
`hook-push` a canonical doc-drift gate.

## Links

- Shepherd story: [shepherd/docs/stories/SH-055-check-docs.md](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-055-check-docs.md)
- Shepherd PR: #58
- Related stories: BC-150 (hook-commit/hook-push fleet tiers)
