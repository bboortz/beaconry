# BC-068 — Defensive cleanup

*Branch: `fix/BC-068-defensive-cleanup`*
*Start: 2026-11-02*

Two cheap items from the BC-067 audit backlog. ~15 lines, zero UX
change, defensive hardening.

## Fixes

### 1. reconcile_scan FD leak

**Before:** `con = sqlite3.connect(DB_PATH)` opened; `try/except
OperationalError` only wrapped the opening SELECT. If an UPDATE
inside the row loop raised `OperationalError` (intermittent DB
lock from a concurrent writer), the exception propagated and the
connection was never closed.

**Fix:** Wrap the full function body in `try/finally con.close()`.
Preserve the existing early-return on cold-boot OperationalError
by returning `[], []` from the inner try block — `finally` still
runs on the way out.

### 2. fix_version blank sanitize

**Before:** `fix_versions = vuln.get("fix", {}).get("versions") or []`
then `fixed_version=fix_versions[0] if fix_versions else "not available"`.
If grype returns `fix.versions: [""]`, we get `fix_versions=[""]`
(truthy because non-empty list) and `fixed_version=""`.

**Fix:** List-comprehension strips blanks:
```python
fix_versions = [v.strip() for v in (vuln.get("fix", {}).get("versions") or []) if v and v.strip()]
```
Legitimate "no fix" cases → `[]` → falls through to `"not available"`.

Mirror the same sanitize in `poc/demo.py::_build_findings_from_matches`
(legacy demo-only path that duplicates the logic — can't refactor
without touching UX, so just keep in sync).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| reconcile_scan closes connection on UPDATE-phase OperationalError | unit | `test_BC_068_defensive_cleanup.py::test_reconcile_scan_closes_connection_on_update_error` | ✓ |
| reconcile_scan closes connection on happy path | unit | `test_BC_068_defensive_cleanup.py::test_reconcile_scan_closes_connection_on_happy_path` | ✓ |
| fix_versions=[""] produces "not available" | unit | `test_BC_068_defensive_cleanup.py::test_fix_versions_blank_strings_filtered` | ✓ |
| fix_versions=[None, ""] produces "not available" | unit | `test_BC_068_defensive_cleanup.py::test_fix_versions_mixed_junk_filtered` | ✓ |
| Legitimate fix_versions=["1.2.4"] still works | unit | `test_BC_068_defensive_cleanup.py::test_fix_versions_normal_entry_unchanged` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

None. Patch bump 0.3.1 → 0.3.2.

## Rollout

- `make bump-patch` → 0.3.2
- Merge to dev
- Homelab live-rebuild — no config changes
