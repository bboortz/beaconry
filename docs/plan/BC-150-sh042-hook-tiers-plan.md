---
id: BC-150
story: docs/stories/BC-150-sh042-hook-tiers.md
status: shipped
---

# BC-150 plan — SH-042 hook-tiers (beaconry side)

**Story:** [BC-150](../stories/BC-150-sh042-hook-tiers.md)

## Design delta

- `Makefile` — add `.PHONY: hook-commit hook-push check-fast`; two 1-line wrappers + a `check-fast: check` forward-compat alias.
- `Makefile → help:` — three new echo lines (hook-commit, hook-push, check-fast) for the BC-016 docs-drift check.
- `VERSION` / `poc/VERSION` / `pyproject.toml` — sync bump `0.22.23 → 0.22.24` (BC-062 verify-version-sync).
- `CHANGELOG.md` — v0.22.24 entry.
- `poc/tests/integration/test_BC_150_hook_tiers.py` — 5 assertions.
- `docs/specs/CAPABILITIES.md` — `CAP-S008` row.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Map `PUSH_STEPS` to existing `check` target | Beaconry's `check` includes lint + type + test + 15 verify-\* + smoke (~7+ min). Too heavy for a ~2 min hook-push budget. |
| Pull SH-023's `check-fast` pytest-xdist split forward | Out of scope; SH-023 / BC-149 is a separate story. |
| Add `check-fast` as a dedicated target with a subset body | Premature — SH-023 will own the subset decision. The alias keeps BC-150 minimal while naming the eventual slot. |

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| hook-commit + hook-push declared .PHONY | integration | `test_BC_150_hook_tiers.py::test_hook_commit_declared_phony` + `::test_hook_push_declared_phony` | ✓ |
| hook-commit dry-run invokes lint/type | integration | `test_BC_150_hook_tiers.py::test_hook_commit_dry_run_resolves` | ✓ |
| hook-push dry-run invokes verify-\* chain | integration | `test_BC_150_hook_tiers.py::test_hook_push_dry_run_resolves` | ✓ |
| check-fast forward-compat alias target | integration | `test_BC_150_hook_tiers.py::test_check_fast_alias_target_exists` | ✓ |
