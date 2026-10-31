# BC-033 — verify_fix scans source_ref, not hardcoded `:latest`

*Branch: `feat/BC-033-verify-fix-uses-source-ref`*
*Date: 2026-10-31*
*Story: `docs/stories/BC-033-verify-fix-uses-source-ref.md`*

## Situation

`verify_fix` (post-fix re-scan) computes `new_image = f"{base}:latest"` and runs syft against it. This was correct **before BC-014** — the pre-BC-014 Fix flow pulled `:latest` as the fix, so scanning `:latest` after was the right verification. BC-014 switched to same-track pulls (`nginx:1.24` → `nginx:1.24`); `verify_fix` was not updated. Dead code assumption from a prior era now actively corrupts verification signal.

## Fix

One line (`new_image = f"{base}:latest"` → `finding.source_ref`), substantial doc comment explaining the BC-014 context so this doesn't rot again. Two unit tests pin the behavior.

## Scope

- `poc/hub/hub.py::verify_fix` — one-line fix + explanatory comment
- `poc/tests/unit/test_hub_helpers.py` — 2 new tests
- `docs/CAPABILITIES.md` — CAP-T076
- `docs/stories/INDEX.md` — BC-033 row

## Breaking changes

Per `docs/plan/_template.md § Breaking changes`:

### 1. API / return shape
None.

### 2. Data model
None.

### 3. Behavioral semantics
**Real behavior change — operator-visible but strictly more honest.** Previously operators who tapped Fix on `nginx:1.24` could have seen "✅ Fixed and verified" if `nginx:latest` happened to be clean, even though their running `nginx:1.24` was still vulnerable. Post-BC-033 they'll see "⚠️ Pulled, but CVE still present upstream" — the truth. No one is worse off; some operators who were falsely reassured will now get accurate state. Pairs with BC-032's viability indicator to pre-warn.

No configuration flag — this is a correctness fix, not an opt-in feature. There's no legitimate use case for keeping the pre-BC-033 behavior.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| verify_fix scans source_ref exactly (frozen tag case) | unit test | `test_hub_helpers.py::test_verify_fix_scans_source_ref_not_latest` | ✓ |
| Works for arbitrary tag shapes (alpine, path with slash, SHA tags) | unit test | `test_hub_helpers.py::test_verify_fix_scans_source_ref_for_custom_tags` | ✓ |
| Non-docker source types short-circuit without invoking syft (Scenario 3) | unit test | `test_hub_helpers.py::test_verify_fix_short_circuits_non_docker_source_type` | ✓ |
| CAP-T076 registered + bidirectional | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |
