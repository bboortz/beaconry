---
id: BC-152
title: verify-index-status drift check (SH-056 mirror)
status: shipped
created: 2026-11-24
branch: feat/SH-056-verify-index-status
plan: docs/plan/BC-152-verify-index-status-plan.md
---

# BC-152 — `verify-index-status` drift check

## Narrative

Agent or human about to push a beaconry change. A story was merged and
coded but the row in `docs/stories/INDEX.md` wasn't flipped from `draft`
or `in-progress` to `shipped` — the frontmatter and the INDEX disagree.
`verify-traceability` doesn't notice because it only reads the
frontmatter. Shepherd hit exactly this (SH-056, PR #55) with 6 stale
rows; beaconry is historically worse (20+ mismatches found on first
run of the gate).

The gate runs at `make check-docs` time (and therefore at `hook-push`
and `ci-branch`), reporting any row that disagrees with its story's
frontmatter, or any shipped+ story missing an INDEX row.

## Trigger

- Developer runs `make verify-index-status` directly, or
- `git push` fires the pre-push hook → `make hook-push` → `check-docs`
  → `verify-index-status`, or
- PR opened → GitHub Actions runs `make ci-branch` → `check-docs`
  → `verify-index-status`.

## Expected outcome

- `make verify-index-status` exits 0 on a clean tree (INDEX ↔ frontmatter
  agree on every story).
- Exits 1 and names the drifting story IDs when any row's status column
  differs from its story's `status:` frontmatter, or when a shipped+
  story has no INDEX row.
- The same script lives on every fleet repo, byte-identical to
  shepherd's reference.

## Out of scope

- Auto-fixing the drift. The script reports; the operator or author
  edits.
- Checking columns other than Status. Title, Branch, Slug drift is
  caught by other gates (or is non-load-bearing).
- Enforcing an INDEX row for `draft` stories. Only `shipped` (and above)
  stories without a row trip the gate — drafts are allowed to be
  INDEX-absent until they move forward.

## Acceptance criteria (Gherkin)

```gherkin
Feature: INDEX.md status column matches each story's frontmatter

  Scenario: verify-index-status target exists
    Given beaconry's Makefile
    When  `grep '^verify-index-status:' Makefile` runs
    Then  the target is declared
    And   .PHONY: verify-index-status is declared
    And   the target body invokes scripts/verify-index-status.sh

  Scenario: script is byte-identical to shepherd reference
    Given scripts/verify-index-status.sh
    And   shepherd's canonical scripts/verify-index-status.sh
    When  sha256 of both is compared
    Then  both digests match

  Scenario: check-docs wires verify-index-status
    Given beaconry's Makefile
    Then  check-docs lists verify-index-status as a prerequisite
    And   the help: block echoes "make verify-index-status"

  Scenario: clean tree passes
    Given docs/stories/INDEX.md matches every story's frontmatter
    When  scripts/verify-index-status.sh runs
    Then  rc=0
    And   output contains "ok"

  Scenario: status mismatch fails
    Given a shipped story whose INDEX row says "draft"
    When  scripts/verify-index-status.sh runs
    Then  rc=1
    And   the output names the story ID
    And   the output includes "frontmatter says 'shipped'"
```

## Capabilities delivered

- **CAP-S010: Fleet-tier verify-index-status drift check (shepherd SH-056)**
  (type: technical, status: active) — see `docs/specs/CAPABILITIES.md`.

Extends: `CAP-S009` (check-docs wrapper) by closing the frontmatter ↔
INDEX gap that `verify-traceability` (per-story, frontmatter-only) misses.

## Links

- Shepherd story: [shepherd/docs/stories/SH-056-verify-index-status.md](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-056-verify-index-status.md)
- Shepherd PR: #59
- Related: BC-151 (check-docs wrapper), BC-024 (verify-traceability reverse check)
