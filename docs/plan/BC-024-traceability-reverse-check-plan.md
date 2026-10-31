# BC-024 — verify-traceability enforces reverse direction

*Branch: `feat/BC-024-traceability-reverse-check`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-024-traceability-reverse-check.md`*

---

## Situation

`verify_traceability.py` enforces four invariants on CAPABILITIES.md ↔ test Covers blocks:

1. Test has `Covers:` block (orphan check).
2. Each CAP in a Covers block exists in CAPABILITIES.md.
3. **Forward**: Each CAP id in a test's Covers → that CAP's `verified by:` must list the test file.
4. CAP's `verified by:` entries must exist on disk.
5. CAP with status=active must have ≥1 verified-by.

Missing invariant: **Reverse**: each test file in a CAP's `verified by:` list → that test file's `Covers:` block must claim the CAP id.

Pattern: four consecutive feat stories (BC-018, BC-020, BC-022, BC-023) added CAP-T054/055/056/060/062/063 and listed test files as verified-by, but forgot to add the CAP ids to those files' Covers blocks. Each three-lens audit caught it manually. The forward direction didn't fire because the test file never *claimed* the CAP in its Covers block — the audit only noticed because a human read both sides.

## Problems to solve

- The reverse-direction drift isn't lint-enforced.
- Human audits are catching it four times in a row — a 100% recurrence rate is a lint-worthy signal.

## Goals

1. Add reverse check to `verify_traceability.py::main`. (Scenarios 1, 2)
2. Avoid double-reporting when the file is already caught as missing-on-disk. (Scenario 3)
3. Deprecated CAPs with no verified-by are not probed. (Scenario 4)
4. Retroactive run against the real repo is clean (post-BC-023 close-gap). (Scenario 5)
5. Error message is copy-pasteable: names CAP id, file path, and the fix hint.

## Scope

- **Modified:** `poc/scripts/verify_traceability.py` — add ~20 LOC reverse-direction block in `main()`.
- **Modified:** `poc/tests/integration/test_BC_004_traceability.py` (existing test suite for this lint) — add Gherkin scenarios 1-4 as new tests.
- **Modified:** `docs/CAPABILITIES.md` — register CAP-T064.
- **Modified:** `docs/stories/INDEX.md` — BC-024 row.

If retroactive run surfaces any drift beyond what I already fixed in BC-023's close-gap, fix in this branch (free regression data).

## Non-goals

- No auto-fix.
- No change to the forward direction.
- No cross-reference to tests covering CAP that's not in verified-by (that's already handled by the forward check).

## Design decisions

- **Resolve test file ref:** reuse the suffix match from the forward check: `rel.endswith(vb) or vb.endswith(rel)`. If no match → file-missing error already fires in the pre-existing check; skip the reverse (avoids double-report, Scenario 3).
- **Deprecated CAPs:** reverse check iterates `verified_by` entries, which is empty for deprecated-no-coverage CAPs. Nothing to check (Scenario 4 is automatic).
- **Reverse-not-collected tests:** if a verified-by file isn't in `test_covers` (orphan test or non-existent file), the forward orphan check already fires. Reverse skips.
- **Error message:** names CAP, file, and the specific fix action ("add '- CAP-X: ...' to Covers OR remove the file from verified-by").

## Breaking changes

None. Pure additive gate.

## Merge plan

1. Branch `feat/BC-024-traceability-reverse-check`
2. Red integration tests (Scenarios 1-4) → red
3. Patch `verify_traceability.py` → green
4. Register CAP-T064, update INDEX, add CAP-T064 to Covers block of the integration test file
5. `make check` green (including retroactive run against the real repo)
6. Fast-forward to `dev`

## Rollout plan

Additive. First `make check` after merge may fail if any plan I missed has reverse drift — fix in this branch so merge-to-dev lands green.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1 — reverse drift fails the gate | integration test | `test_BC_004_traceability.py::test_reverse_covers_omission_fails` | ✓ |
| Scenario 2 — both directions covered passes | integration test | `test_BC_004_traceability.py::test_reverse_both_directions_covered_passes` | ✓ |
| Scenario 3 — no double-report with file-missing error | integration test | `test_BC_004_traceability.py::test_reverse_skipped_when_file_missing` | ✓ |
| Scenario 4 — deprecated CAP exempt | integration test | `test_BC_004_traceability.py::test_reverse_deprecated_cap_exempt` | ✓ |
| Scenario 5 — retroactive run on real repo clean | live run | `make verify-traceability` output | ✓ |
| CAP-T064 registered + bidirectional (self-reference!) | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

## Testing strategy

- **Integration tests** build tmp fixture dirs: a tiny CAPABILITIES.md, a tiny `tests/unit/test_x.py` with controlled Covers block, invoke `verify_traceability.main()` directly (not subprocess) and assert on returncode + stderr capture.
- **Retroactive** against real `poc/` already runs via `make verify-traceability` in `make check`.

## Testing commands

```bash
source .venv/bin/activate
pytest poc/tests/integration/test_BC_004_traceability.py -v --no-cov -k reverse
make check   # includes the retroactive run
```
