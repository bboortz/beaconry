# BC-041 — verify-evidence-pointers reverse check

*Branch: `feat/BC-041-evidence-reverse-check`*
*Start date: 2026-11-01*
*Story: `docs/stories/BC-041-evidence-reverse-check.md`*

---

## Situation

`verify_evidence_pointers.py` enforces one direction: every ✓ row in a plan's
shipping-evidence table points at a real pytest node. The reverse — every
integration test is pointed at by some row — isn't enforced. Partial close-gaps
(a developer adds a test but forgets the plan row) slip through CI unseen.
BC-031's close-gap and BC-040's post-ship audit both surfaced the same pattern.

---

## Problems to solve

- A `test_BC_NNN_*.py` can carry test functions that no plan row references.
- Pre-ship audit "all green" doesn't catch this class of miss.
- No mechanism to allowlist intentional helpers (fixture-only functions,
  cross-BC smoke tests) without encoding them in the test name.

---

## Goals

1. Add a reverse pass to `verify_evidence_pointers.py`: for every
   `poc/tests/integration/test_BC_NNN_*.py`, every `test_*` function must be
   referenced by a shipping-evidence pointer in the matching
   `docs/plan/BC-NNN-*-plan.md`.
2. Explicit allowlist for grandfathered or intentional orphans. One module-
   level `REVERSE_CHECK_ALLOWLIST: dict[str, str]` — reason per entry.
3. Clean error message: name the orphan node id + the plan it should be in +
   a copy-pasteable `| description | pointer | ✓ |` row template.
4. Fast-tier gate: `make verify-evidence-pointers` fails if any orphan exists.
5. Audit shipped BCs; backfill plan rows for anything that currently fails
   OR allowlist with "pre-BC-041 — grandfathered" if the test is intentional.

---

## Scope

- `poc/scripts/verify_evidence_pointers.py` — new `_reverse_check` function;
  new `REVERSE_CHECK_ALLOWLIST` constant; `main()` runs both passes.
- `poc/tests/unit/test_verify_evidence_pointers.py` — add 6 unit tests covering
  the reverse logic + 1 live-state regression guard.
- `docs/CAPABILITIES.md` — register CAP-T088 + CAP-B025.
- Each `docs/plan/BC-*-plan.md` that currently has orphan tests either gets
  a row added OR an allowlist entry (per-test decision).

---

## Non-goals

- Unit test reverse coverage. Unit tests cover shared helpers; a 1:1 plan
  mapping would create noise without signal.
- Enforcing Gherkin scenario → test mapping (BC-008's territory).
- Row description vs test docstring equality (low signal).

---

## Design decisions

- **Integration tests only (`poc/tests/integration/test_BC_*.py`)**: unit
  tests often cover helpers used across BCs. The scope note in IDEAS was
  explicit on this.
- **Match by `test_name` only, not full `file.py::test_name` pointer**: plans
  sometimes write `test_x.py::test_fn`, sometimes bare `test_fn`. For reverse
  check, "is this function referenced *anywhere* in the matching plan?" is
  the right question — exact-pointer-form shouldn't matter.
- **Allowlist on the `test_*` function name, not node id**: parametrized
  tests collect as `test_x[case1]`, `test_x[case2]`; the allowlist entry
  for `test_x` covers all parametrizations without wildcard syntax.
- **Soft-pass if the plan file is missing**: BC-019 already warns on missing
  plans for `feat/*` branches; reverse check doesn't double-flag.
- **Soft-pass if plan file has no `## Shipping evidence` section**: again,
  BC-019 is the plan-doc completeness gate; reverse check assumes a shipped
  plan.

---

## Breaking changes

### 1. API / return shape
None. Script's exit codes unchanged (0 clean, 1 drift).

### 2. Data model
None. No DB, no schema.

### 3. Behavioral semantics
- Developers who do nothing: fast tier now also enforces reverse coverage.
  Initial pass of this branch brings the live repo clean (backfill rows OR
  allowlist). Future PRs must align.
- The audit protocol in `memory/workflow.md § DD-TGD 6a` already requires
  evidence rows for every scenario; this story is the mechanical enforcer.

---

## Merge plan

1. All work on `feat/BC-041-evidence-reverse-check`.
2. `make check` + `make check-full` pass.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward.
5. Post-merge: story → `shipped`, INDEX update.

---

## Rollout plan

Additive. No new env var, no new make target. The existing
`make verify-evidence-pointers` fast-tier gate gains a second pass. First
run on existing branches will surface current orphans; this story resolves
them in-tree before shipping.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Clean-state live repo passes both passes | integration | `test_BC_041_reverse_check.py::test_shipped_repo_passes_reverse_check` | ✓ |
| Orphan integration test → violation names the function + plan + template | integration | `test_BC_041_reverse_check.py::test_orphan_test_fires_violation` | ✓ |
| Allowlisted function → no violation | integration | `test_BC_041_reverse_check.py::test_allowlist_suppresses_violation` | ✓ |
| Integration test with no matching plan → soft-pass | integration | `test_BC_041_reverse_check.py::test_missing_plan_soft_passes` | ✓ |
| Plan with no shipping-evidence section → soft-pass | integration | `test_BC_041_reverse_check.py::test_plan_without_evidence_section_soft_passes` | ✓ |
| Parametrized test → covered by its base name | integration | `test_BC_041_reverse_check.py::test_parametrized_test_matched_by_base_name` | ✓ |
| Pointer-form row (referenced test passes) | integration | `test_BC_041_reverse_check.py::test_referenced_test_passes` | ✓ |
| Multiple orphans in one file all surface | integration | `test_BC_041_reverse_check.py::test_multiple_orphans_all_reported` | ✓ |
| `_` prefixed helpers are skipped | integration | `test_BC_041_reverse_check.py::test_private_underscore_prefix_skipped` | ✓ |
| Reverse check wired into `make check` | multi-gate | `make check` includes the step | ✓ |
| CAP-T088 + CAP-B025 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

Integration tests use isolated temp directories with crafted plan + test
files; no reliance on live repo state in the unit cases. One live-repo
regression guard asserts the shipped tree passes both passes.

---

## Testing commands

```bash
make check
make verify-evidence-pointers
pytest poc/tests/integration/test_BC_041_reverse_check.py -v
```
