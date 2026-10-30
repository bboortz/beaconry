# BC-012 close-gap — pyright actually enforces the trap guard

*Branch: `fix/BC-012-close-trap-pyright-assertion`*
*Start date: 2026-10-30*

## Situation

BC-012 (shipped `c72c9b0` to dev earlier today) claimed "the restart-loop trap is a type error, not a runtime pathology". The CAP-T041 verification was `test_beaconry_health_trap.py::test_livez_and_readyz_have_distinct_signatures` — but that assertion only catches ONE degenerate case: someone collapsing the aliases to the same object (`LivezProvider = ReadyzProvider`). It does NOT catch the subtler and more realistic case where someone redefines both aliases to the same type signature (e.g. `Callable[[], dict[str, str]]` for both) — pyright would accept the trap-reopening wiring silently and my runtime shadow would still pass.

The post-ship audit flagged this as a Lens 1 gap (Gherkin scenario 3's "pyright emits an error naming the type incompatibility" wasn't actually test-asserted). User picked option (a): close with a real pyright invocation.

## Problem to solve

CAP-T041's compile-time guarantee is not test-asserted. If the restart-loop trap guard regresses, no gate catches it.

## Goal

Add an integration test that writes a fixture file misusing `build_router(livez_provider=<readyz-shaped>, …)`, invokes pyright on it, and asserts pyright emits an error naming the livez slot. Plus a negative control (fixture using build_router correctly must pass pyright clean). Plus a drift alarm (production aliases match the fixture's inlined copies).

## Scope

- `poc/tests/integration/test_BC_012_trap_pyright_enforcement.py` — three tests:
  - `test_fixture_aliases_match_production_aliases` — drift alarm
  - `test_pyright_rejects_readyz_shaped_fn_in_livez_slot` — the main compile-time assertion
  - `test_pyright_accepts_correct_usage` — negative control
- `docs/CAPABILITIES.md` — refresh CAP-T041 "verified by" list to add the new test; description reflects that enforcement is now a real pyright invocation, not just a runtime shadow

## Non-goals

- Changing the trap guard semantics
- Adding more probes to beaconry_health
- Any BC-010 / BC-011 / BC-013 work

## Design decisions

**Self-contained fixture code (inlined type aliases) + drift alarm test.** Rejected: fixture imports from `poc.beaconry_health`. Reason: pyright resolution against tmp_path fixtures is fiddly; inlining the aliases keeps the test pyright-isolatable. The drift alarm (`test_fixture_aliases_match_production_aliases`) prevents the fixture from silently going stale when the real aliases change.

**Negative control test required.** Rejected: just assert pyright complains on the misuse. Reason: without a correct-usage test, we can't distinguish "pyright rejected because of the trap guard" from "pyright rejected because of unrelated noise in the fixture environment".

**Skip when pyright not on PATH.** Rejected: hard-fail. Reason: `make check` already requires pyright, so the normal gate path has it; a bare-dev environment without pyright shouldn't block the whole suite.

## Breaking changes

None. Pure test addition + CAP description refresh.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| pyright actually rejects readyz-shaped probe in livez slot | integration test | `test_pyright_rejects_readyz_shaped_fn_in_livez_slot` | ✓ |
| negative control: pyright accepts correct usage | integration test | `test_pyright_accepts_correct_usage` | ✓ |
| drift alarm: fixture aliases match production | integration test | `test_fixture_aliases_match_production_aliases` | ✓ |
| CAP-T041 description refreshed | verify-traceability | CAPABILITIES.md § CAP-T041 names new test | ✓ |

## Testing commands

```bash
source .venv/bin/activate
pytest poc/tests/integration/test_BC_012_trap_pyright_enforcement.py -v --no-cov
make check
```
