# fix/BC-003-close-gaps — Plan

*Branch: `fix/BC-003-close-gaps`*
*Start date: 2026-10-30*

---

## Situation

Second audit of BC-003 after shipping found two things:

1. **Missing row in BC-003's Shipping evidence table.** The Gherkin has a `real_tools integration test passes` scenario; my evidence table in the plan doc had 10 rows covering other scenarios but not this one. The automated check (CAP-T027) didn't catch it because it enforces row completeness, not Gherkin-to-table completeness.
2. **The automation gap is systemic.** `check_shipping_evidence` only verifies each row has a non-placeholder pointer and ✓ status. It doesn't count Gherkin `Scenario:` lines in the story and compare to the plan's table length. The explicit "Rejected: Gherkin parse-and-match" non-goal from BC-008-automate-audit just bit us.

## Problems to solve

- BC-003's claim of "every row ✓" is misleading because the table is missing a row the Gherkin names
- The automated audit has a one-way blind spot: table → coverage is enforced; story → table coverage is not

## Goals

1. Run `pytest -m real_tools` with syft+grype on PATH and nginx:1.24 pulled; capture pass/fail
2. Add the missing row to BC-003's Shipping evidence table with a dated pointer
3. Promote `check_shipping_evidence` to parse Gherkin scenarios in the story file and warn (not fail) when the plan's evidence table has fewer rows than the story's scenario count
4. CAP-T027 description refreshed to name the new behavior
5. Regression test for the scenario-count check

## Scope

- `poc/scripts/verify_traceability.py` — add `_count_gherkin_scenarios(story_text)` + extend `check_shipping_evidence` with a scenario-count sanity check (warning only — doesn't fail the gate but surfaces to stderr)
- `poc/tests/integration/test_BC_004_traceability.py` — regression test
- `docs/plan/BC-003-live-verified-buttons-plan.md` — add the real_tools row with evidence pointer
- `docs/CAPABILITIES.md` — CAP-T027 description refresh
- This plan doc with its own filled Shipping evidence table (self-dogfood)

## Non-goals

- Hard-fail on scenario-count mismatch (warning only; mismatches may be legitimate — a story may group multiple scenarios into one plan row)
- Full Gherkin AST parsing (just `^\s*Scenario:` line count is enough)
- Retroactively closing scenario-count gaps for BC-001..BC-002 or BC-004..BC-008 plans (grandfathered)

## Design decisions

**Warning, not failure, on scenario-count mismatch.** A plan may legitimately roll multiple Gherkin scenarios into one evidence row (e.g., all 3 "live button" scenarios under one "live tap session" row). Hard-failing would create friction. Warning surfaces the mismatch at `make verify-traceability` run but doesn't block.

**Count only lines matching `^\s*Scenario:`.** Simple, no AST, matches current authoring convention. If the story's Gherkin has 8 Scenarios and the plan's table has only 4 rows, warn.

**Rejected: parse scenario titles and match them 1:1 to row content.** Too heuristic; synonym matching etc. Overkill for warning-only.

## Breaking changes

None — new behavior is advisory only.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| `pytest -m real_tools` runs green | live manual | ran 2026-10-30 in this session, see commit message | ✓ |
| Missing BC-003 row added with evidence | plan diff | BC-003 plan "Phase 3" section grows row #11 | ✓ |
| verify-traceability warns on scenario-count mismatch | integration test | `test_shipping_evidence_warns_on_scenario_count_mismatch` | ✓ |
| Mismatch warning doesn't fail the gate | integration test | same test asserts rc == 0 | ✓ |
| CAP-T027 description refreshed | verify-traceability | 40 CAPs clean after update | ✓ |
| This fix branch's own evidence table is itself complete | this plan | above table | ✓ |

## Merge plan

1. Plan + implementation + tests + evidence row
2. `make check` + `make check-full` pass; verify-traceability clean
3. Fast-forward merge to dev

## Testing strategy

- Run `pytest -m real_tools` manually to confirm green with current deps; attach to the evidence row
- New integration test with a synthetic story that has 5 Scenarios and a plan with 3 rows → warning in stderr, rc == 0
- Existing scenario-count-less test continues to pass

## Testing commands

```bash
source .venv/bin/activate
pytest -m real_tools -v           # must be green
make check                         # fast tier
make check-full                    # full tier + opt-in markers
```
