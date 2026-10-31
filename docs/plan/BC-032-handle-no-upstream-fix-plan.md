# BC-032 close-gap — handle "not available" fixed_version gracefully

*Branch: `fix/BC-032-handle-no-upstream-fix`*
*Parent: `docs/plan/BC-032-fix-viability-indicator-plan.md`*
*Date: 2026-10-31*

## Situation

Operator screenshot 2026-10-31 of 🔥 FIX REGRESSED for CVE-2023-44487:

```
Package: nginx 1.24.0-1~bullseye → fixed in not available
Fix viability: ❌ unlikely — frozen tag '1.24' — pulling returns the
  same bits; edit compose to use a newer tag that ships not available
```

Grype's `process_sbom_entry` writes the sentinel string `"not available"` as `fixed_version` when `vuln["fix"]["versions"]` is empty. BC-032 shipped without guarding this case, so both:

1. The Package line renders "fixed in not available"
2. The viability reason inlines the sentinel: "edit compose to use a newer tag that ships not available"

Both are nonsensical. Per `breaking-changes-in-planning` discipline, this is a UX defect fixed in-branch, not deferred.

## Fix

1. Extract `_has_upstream_fix(finding)` helper that detects the sentinel.
2. `assess_fix_viability` guards it FIRST (before tag mutability) and returns `unlikely` with reason "no upstream fix available yet — the vendor hasn't released a patched version". Regardless of docker tag or source_type.
3. New `_package_fix_line` helper renders either `→ fixed in X` (upstream has fix) or `— no upstream fix available yet` (sentinel).
4. Both notification formatters use `_package_fix_line` instead of inlining the string.

## Breaking changes

Per `docs/plan/_template.md § Breaking changes`:

### 1. API / return shape
`assess_fix_viability`'s label semantics changed slightly: alpm + "not available" used to return `unknown`, now returns `unlikely`. Rationale: `unknown` was wrong — we DO know Fix can't help if no upstream patch exists. One existing test (`test_viability_alpm_without_fixed_version_is_unknown`) renamed + rewritten to assert the correct new behavior.

### 2. Data model
None. `_has_upstream_fix` reads existing `fixed_version` field.

### 3. Behavioral semantics
Notification bodies render differently when upstream has no fix (nicer phrasing, no "not available" leak). Operator-visible improvement; no caller breaks.

## Shipping evidence

| Scenario / goal | Pointer | Status |
|---|---|---|
| No upstream fix → unlikely regardless of tag | `test_viability_no_upstream_fix_is_unlikely_regardless_of_tag` | ✓ |
| alpm with no upstream fix → unlikely (was unknown, correctness fix) | `test_viability_alpm_without_fixed_version_is_unlikely` | ✓ |
| alpm + no upstream fix body phrasing | `test_viability_alpm_no_upstream_fix_is_unlikely` | ✓ |
| `format_notification` body: "fixed in not available" NEVER appears | `test_format_notification_handles_no_upstream_fix_gracefully` | ✓ |
| `format_regression` body: "ships not available" NEVER appears | `test_format_regression_handles_no_upstream_fix_gracefully` | ✓ |
| make check green | multi-gate | ✓ |
