---
id: BC-000
title: Repo + development process scaffold
status: in-progress
created: 2026-10-29
branch: feat/BC-000-repo-scaffold
plan: docs/plan/BC-000-repo-scaffold-plan.md
---

# BC-000: Repo + development process scaffold

## Narrative

The operator (also the developer, during the POC phase) sits down at a fresh clone of the beaconry repo. They want to go from `git clone` to a running, testable system in under 10 minutes with zero process ambiguity: branch policy, PR target, test gates, story templates, and tooling are all already in place and documented in `CLAUDE.md` + `memory/workflow.md`. This story is the foundation every future story depends on.

## Trigger

A new developer (or future-self) opens the repo for the first time, or an existing contributor needs to confirm "how do we work here" without commit archaeology.

## Expected outcome

- `git clone && make check` either passes or fails with a named gate + fix hint
- Branch policy, PR flow, gate matrix are documented in `CLAUDE.md` + `memory/workflow.md`
- Story template + 8-question clarification set exist in `docs/stories/`
- `INDEX.md` lists BC-000 and marks it `shipped` once this branch merges
- `main` branch exists with an initial scaffold commit; `dev` branch exists; `feat/BC-000-repo-scaffold` demonstrates the flow
- Pre-commit framework config present; `pre-commit install` wires both `pre-commit` and `pre-push` hooks

## Out of scope

- GitHub remote and GitHub Actions CI pipeline (deferred until repo goes remote)
- The POC demo pipeline itself (that's BC-001)
- `demo.sh --smoke` real implementation (stub only in BC-000 — real smoke in BC-001)
- OSS release packaging
- Any production/CI promotion automation

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scaffolded repo + development process

  Scenario: Fresh clone runs local gates
    Given a fresh clone of the repo
    And the developer has created a venv and installed requirements-dev.txt
    When the developer runs "make check"
    Then every gate (lint, format, type, test) either passes or fails with a named gate and fix hint

  Scenario: Pre-commit blocks a lint violation
    Given the pre-commit framework is installed via "pre-commit install"
    When the developer stages a Python file containing a ruff violation
    And attempts "git commit"
    Then the commit is blocked by the pre-commit hook with the ruff error visible

  Scenario: Pre-push blocks a failing test
    Given the pre-push hook is installed via "pre-commit install --hook-type pre-push"
    When the developer attempts "git push" while a pytest test is failing
    Then the push is blocked with the failing test name visible

  Scenario: Story template and 8-question guide exist
    Given docs/stories/ exists
    When the developer opens _template.md and _8-questions.md
    Then both files are present
    And INDEX.md lists BC-000

  Scenario: Branch structure matches policy
    Given the repo has been bootstrapped
    When "git branch" is run
    Then branches include "main" and "dev"
    And "main" has only the initial scaffold commit
```

## Open questions

- (none — resolved during the 2026-10-29 clarification session)

## Links

- Design: `DESIGN.md` (full doc applies — this is foundation)
- Process: `memory/workflow.md`, `memory/code-quality.md`
- Related: BC-001 (POC demo depends on this scaffold)
