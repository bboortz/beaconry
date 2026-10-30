# fix/enforce-plan-docs — Plan

*Branch: `fix/enforce-plan-docs`*
*Start date: 2026-10-30*

---

## Situation

CLAUDE.md and `memory/code-quality.md` both state unconditionally that every `feat/*` and `fix/*` branch needs a plan doc at `docs/plan/<slug>-plan.md`. Across this session, six fix branches shipped without plan docs. The rule has no automated enforcement, so slips go unnoticed until a user audit (this one). Same pattern as the capability-list hypocrisy caught in BC-004.

## Problems to solve

- Rule exists on paper, no code enforcement
- Human reviewers (and Claude) silently slip
- The slip only surfaces during a cross-cutting audit, not at the branch level

## Goals

1. `verify-traceability` fails on `make check` and on `git push` when the current branch matches `feat/<slug>` or `fix/<slug>` and `docs/plan/<slug>-plan.md` doesn't exist
2. When not on a `feat/*` / `fix/*` branch (`main`, `dev`, `chore/*`, `docs/*`, `refactor/*`, detached HEAD, CI without a branch), the check is a no-op — never false-positives
3. Clear error message: names the branch, names the expected plan-doc path, nudges to the template
4. Unit test coverage: happy path (plan exists), missing plan (fail), non-feat/fix branch (skip), detached HEAD (skip), fix on this very branch (meta — the plan doc for this branch satisfies the gate on first commit)
5. Already-merged fix branches without plan docs are grandfathered — no retroactive work

## Scope

- `poc/scripts/verify_traceability.py` — new `check_branch_plan_doc()` function + wire into `run()`; add optional `branch` kwarg for testability
- `poc/tests/integration/test_BC_004_traceability.py` — new tests for the branch-plan check (grouped with existing traceability tests since both live in verify-traceability)
- `docs/CAPABILITIES.md` — new CAP-T022 ("Fix/feat branches without a plan doc fail the gate")
- This plan doc

## Non-goals

- Retroactively creating plan docs for the six already-merged fix branches (grandfathered)
- Enforcing plan doc *content* (just presence)
- Enforcing a specific template structure in the plan doc
- Rejecting bare `fix` or `feat` branch names (e.g. `fix/something-random`) that don't have a hyphen — same filename resolution
- Branch-level enforcement via git hooks (keeps the mechanism in one place: verify-traceability)

## Design decisions

**Rule carried in verify-traceability, not a separate script.** Matches the "one entrypoint for process invariants" pattern. User's chosen option.

**Pure function + injectable branch.** `check_branch_plan_doc(branch, docs)` is a pure function; `get_current_branch()` is called by `run()` when no branch is passed. Tests pass a synthetic branch name.

**Skip gracefully for all branches other than `feat/*` / `fix/*`.** Keeps the gate out of chore/refactor/docs/dev/main workflows where it's not required.

**Slug derivation = everything after the first slash.** For `feat/BC-005-compose-multi-image` → slug `BC-005-compose-multi-image` → file `docs/plan/BC-005-compose-multi-image-plan.md`. Matches existing convention.

**Rejected: enforce via pre-commit git hook that blocks the first commit.** More invasive, harder to test, duplicates logic across hook config and script. One source of truth = better.

## Breaking changes

- Any future `fix/*` or `feat/*` branch missing a plan doc fails `make check` and the pre-push hook.
- The six already-merged fix branches without plan docs continue to work (grandfathered — not on any branch's HEAD anymore).

## Merge plan

1. Plan doc + implementation + tests on `fix/enforce-plan-docs`
2. `make check` passes locally (including this branch's own plan-doc check)
3. Fast-forward merge to `dev`
4. Delete branch

## Rollout plan

Additive. First-time developer running `make check` on a new `fix/<slug>` branch sees the clear error and creates the plan doc from `docs/plan/_template.md`.

## Testing strategy

- Unit test: branch `fix/foo` + plan at `docs/plan/foo-plan.md` → passes
- Unit test: branch `fix/foo` + no plan → fails with helpful message
- Unit test: branch `dev` or `main` → check is no-op
- Unit test: detached HEAD / empty branch → check is no-op
- Live verification: on this branch (`fix/enforce-plan-docs`), the plan doc at `docs/plan/enforce-plan-docs-plan.md` exists → `make check` passes

## Testing commands

```bash
source .venv/bin/activate
make check                                   # should pass; this branch has its plan doc
pytest poc/tests/integration/test_BC_004_traceability.py -v    # includes new branch-plan tests
```
