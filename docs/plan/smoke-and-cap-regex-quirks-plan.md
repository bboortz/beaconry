# fix/smoke-and-cap-regex-quirks — Plan

*Branch: `fix/smoke-and-cap-regex-quirks`*
*Start date: 2026-10-30*

---

## Situation

A verification pass on dev after BC-005 shipped surfaced two defects:

1. **`make smoke` is broken.** `poc/demo.sh --smoke` doesn't force `BEACONRY_MOCK=1`. Before BC-005, `poc/demo.py` defaulted to mock mode on import, so the smoke path was mocked implicitly. BC-005 introduced real-mode-by-default dispatch and updated the Makefile but not demo.sh. Current behavior: `make smoke` tries to run the real pipeline, demands syft/grype on PATH, exits 2.
2. **CAP-id regex in verify-traceability truncates at the first hyphen.** `\bCAP-[A-Za-z0-9]+\b` matches `CAP-DOES` from `CAP-DOES-NOT-EXIST`. Current CAP ids (`CAP-B001`, `CAP-T022`) are single-segment so this never surfaces in production, but error messages for typos are misleading, and any future convention using multi-segment ids would silently misreport.

## Problems to solve

- Smoke mode doesn't exercise what it claims (CI gate fails on syft/grype absence rather than running the mocked pipeline)
- Regex error messages don't match the string the operator typed
- No regression test for smoke mode

## Goals

1. `make smoke` exits 0 with the mocked BC-001 pipeline output
2. CAP-id regex accepts multi-segment ids (`CAP-FOO-BAR`); existing single-segment ids still match
3. Regression test: smoke mode doesn't crash and reports mock-mode behavior
4. Regression test: dangling multi-segment CAP id gets the full id in the error, not a truncation

## Scope

- `poc/demo.sh` — `--smoke` branch adds `export BEACONRY_MOCK=1` before `exec`
- `poc/scripts/verify_traceability.py` — `CAP_ID_RE` grows to `r"\bCAP-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b"`; `CAP_HEADING_RE` similarly
- Regression tests in `poc/tests/integration/test_BC_004_traceability.py` for the regex fix
- This plan doc

## Non-goals

- Changing CAP-id naming convention (single-segment stays the default)
- Restructuring smoke mode beyond the env flag (don't rewrite demo.sh)
- Adding a general bash-script test harness

## Design decisions

**Smoke always = mock.** The entire point of smoke is to validate the pipeline *mechanically* without network / Docker / Telegram. Any future "live smoke" path should be a different target (e.g. `make smoke-live`). Keeping smoke mocked is the predictable contract.

**Regex extended, not replaced.** `CAP-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*` is a superset of the current one — existing ids still match, multi-segment ids now also match. No corpus migration.

## Breaking changes

None. Both changes are compatible extensions.

## Merge plan

1. Plan doc first commit
2. Smoke fix + regex fix + regression tests on this branch
3. `make check` passes
4. `make smoke` exits 0
5. Fast-forward merge to dev; delete branch

## Rollout plan

Additive. No operator action required.

## Testing strategy

- `make smoke` is run manually as part of verification (not in pytest — bash script).
- Unit test: `CAP_ID_RE` matches multi-segment id correctly (parametrised against `CAP-A`, `CAP-B001`, `CAP-FOO-BAR`).
- Integration test: dangling `CAP-FOO-BAR` reference → full id appears in stderr, not truncated.

## Testing commands

```bash
source .venv/bin/activate
make check
make smoke       # exits 0 with "Demo complete — 2/2 findings fixed and verified"
```
