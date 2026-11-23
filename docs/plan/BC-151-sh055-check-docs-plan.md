# BC-151 — SH-055 check-docs wrapper (beaconry side)

*Branch: `feat/SH-055-check-docs`*
*Start date: 2026-11-23*
*Story: `docs/stories/BC-151-sh055-check-docs.md`*
*Shepherd story: [SH-055](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-055-check-docs.md) — PR #58*

---

## Situation

Each fleet repo already has drift-check logic under different target names
(shepherd: `verify-traceability verify-memory-size`; beaconry: six
`verify-*` targets; HRC: none yet; etc.). Shepherd SH-055 unifies the
*wrapper* name — `make check-docs` — so a human or agent on any fleet repo
asks the same question with the same incantation. Beaconry's six drift
gates already run inside `make check` (fast tier), but there is no single
entry point and `hook-push` doesn't run them: a doc-only change that
doesn't flip the VERSION sync can slip past the pre-push gate and land
staleness on `dev`.

---

## Problems to solve

- No single doc-drift entry point on beaconry.
- `hook-push` (SH-042 / BC-150) runs `verify-version-bumped`,
  `verify-changelog`, `verify-feature-complete` but misses the other six
  drift gates.
- `ci-branch` (post-SH-029) runs `lint test` only — doc drift surfaces
  only via the nightly full `make check`.

---

## Goals

1. Add `check-docs` as a thin wrapper that delegates to beaconry's six
   existing verify-\* drift targets (maps 1:1 to the Gherkin acceptance
   "check-docs runs the six delegate targets").
2. Wire `check-docs` into `PUSH_STEPS` (maps to "PUSH_STEPS and
   BRANCH_STEPS include check-docs").
3. Introduce `BRANCH_STEPS` and route `ci-branch` through it, so CI on PR
   also runs the gate.

---

## Scope

- `Makefile`:
  - `.PHONY`: add `check-docs`.
  - `help:`: one new echo line in the Tiers block (BC-016 docs-drift
    requires every .PHONY target be listed in `help:`).
  - `check-docs:` target body with the six delegate prereqs.
  - `BRANCH_STEPS ?= lint test check-docs` declared next to `ci-branch:`.
  - `PUSH_STEPS`: insert `check-docs` after `check-fast`.
  - `ci-branch:` prereqs re-routed through `$(BRANCH_STEPS)`.
- `poc/tests/integration/test_BC_151_check_docs.py` — 8 assertions.
- `docs/specs/CAPABILITIES.md` — new `CAP-S009` row.
- `docs/stories/BC-151-sh055-check-docs.md` — this story.
- `docs/plan/BC-151-sh055-check-docs-plan.md` — this plan.
- `docs/plan/SH-055-check-docs-plan.md` — pointer doc (branch name
  `feat/SH-055-check-docs` requires a matching plan slug; same precedent
  as `docs/plan/SH-042-hook-tiers-plan.md`).
- `VERSION` / `poc/VERSION` / `pyproject.toml` — sync bump (BC-062).
- `CHANGELOG.md` — new version entry.

---

## Non-goals

- Changing the delegate targets' bodies.
- Renaming beaconry's drift targets to match any other fleet repo.
- Adding `check-docs` to `hook-commit` (~15 s budget is too tight).
- Making the gate behave differently on CI vs local (same body both sides).

---

## Design decisions

- **Wrapper is pure delegate, no new logic.** Each of the six prereqs
  already runs inside `make check`; the wrapper just names the subset
  that answers "are the docs consistent?" so pre-push and CI-on-PR can
  invoke it directly.
- **Introduce `BRANCH_STEPS` rather than inlining `check-docs` into
  `ci-branch`.** Matches shepherd's shape (SH-055 shepherd side) so a
  future fleet-wide `mk/ci.mk` lift can share the variable name.
- **`BRANCH_STEPS` declaration sits next to `ci-branch:`.** Make expands
  prerequisites at parse time; declaring the variable after `ci-branch:`
  would leave the prereq list empty. Verified by `make -n ci-branch`.
- **Help-block echo in the Tiers section.** BC-016 `verify-docs-drift`
  requires every `.PHONY` target to be referenced in `help:`.

### Alternatives rejected

| Alternative | Why not |
|---|---|
| Expand beaconry's `check` to be the fleet `check-docs` | `check` also runs `lint type test smoke` — far too heavy for a pre-push hook; conflates two tiers. |
| Keep `ci-branch: lint test` and add `check-docs` as sibling prereq | Works, but drifts from the shepherd shape (`BRANCH_STEPS` list). Fleet-wide `mk/ci.mk` is coming in a follow-up. |
| Reuse `verify-traceability` as the entry point | Doesn't catch the other five drift classes (version-sync, install-doc, docs-drift, docs-layout, evidence-pointers). Would still leave BC-062 incidents slipping through. |

---

## Breaking changes

### 1. API / return shape

None. `check-docs` is a new target; all existing `make <target>`
invocations behave identically. `ci-branch`'s prerequisites change
compositionally but still include `lint` and `test`.

### 2. Data model (schema + legacy rows)

None — no DB changes.

### 3. Behavioral semantics

- **Noisier pre-push.** Six additional verify-\* scripts now run on every
  push (was: three). All six already run inside `make check` (fast tier),
  so this is strictly a stage shift, not a new cost. Combined runtime
  stays well within the SH-042 `hook-push` ~2 min budget (measured <5 s
  for the full six-chain on a clean tree).
- **Noisier CI on PR.** `ci-branch` adds the same six gates. Runtime
  budget 5 min; measured chain is sub-second.

No operator-visible notification changes.

---

## Merge plan

1. All work on `feat/SH-055-check-docs` (branch name matches shepherd
   story ID; BC-151 is beaconry's tracking ID, same convention as
   `feat/SH-042-hook-tiers` ↔ BC-150).
2. `make check` passes on branch tip.
3. PR to `dev`; admin-merge after CI green (user granted authority).
4. Merge strategy: merge commit (fleet default; preserves PR boundary).
5. Post-merge: story status already `shipped` on first commit.

---

## Rollout plan

None — change is additive. Developers running `make check-docs` on an
old clone see "no rule to make target" until they pull. No operator
action required.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| `.PHONY: check-docs` declared | integration | `test_BC_151_check_docs.py::test_check_docs_declared_phony` | ✓ |
| `check-docs:` target body exists | integration | `test_BC_151_check_docs.py::test_check_docs_target_exists` | ✓ |
| check-docs prereqs are the six delegates | integration | `test_BC_151_check_docs.py::test_check_docs_has_six_delegates` | ✓ |
| `make -n check-docs` invokes all six verify-\* modules | integration | `test_BC_151_check_docs.py::test_check_docs_dry_run_invokes_all_delegates` | ✓ |
| PUSH_STEPS includes check-docs | integration | `test_BC_151_check_docs.py::test_push_steps_includes_check_docs` | ✓ |
| BRANCH_STEPS includes check-docs | integration | `test_BC_151_check_docs.py::test_branch_steps_includes_check_docs` | ✓ |
| `make -n hook-push` runs the check-docs chain | integration | `test_BC_151_check_docs.py::test_hook_push_dry_run_includes_check_docs` | ✓ |
| `make -n ci-branch` runs the check-docs chain | integration | `test_BC_151_check_docs.py::test_ci_branch_dry_run_includes_check_docs` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | N/A — purely additive, no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

Pure Makefile-shape verification via regex + `make -n` dry-runs. No
runtime side-effects required; each delegate already has its own
dedicated BC-* test asserting the actual drift-detection behavior.

---

## Testing commands

```bash
.venv/bin/pytest poc/tests/integration/test_BC_151_check_docs.py --no-cov -v
```

Expected output:

```
8 passed in ~1.5s
```
