---
id: BC-071
title: Fix CI env leak — BEACONRY_SKIP_VERSION_BUMP shouldn't be set during `make check`
status: shipped
created: 2026-11-02
branch: fix/BC-071-ci-env-leak
plan: docs/plan/BC-071-ci-env-leak-plan.md
---

# BC-071: CI env leak

First real GitHub Actions CI run on `dev` (BC-069's workflow)
failed. Two tests false-failed because the CI step set
`BEACONRY_SKIP_VERSION_BUMP=1` as an env var, which leaked into
pytest subprocesses and made `verify_version_bumped.check()`
return `[]` instead of the expected error list.

## Fix

1. Dropped `BEACONRY_SKIP_VERSION_BUMP` from the CI yaml's `make
   check` env block — not needed (`make check` doesn't gate on
   `verify-version-bumped`; that's a pre-push hook).
2. Hardened both affected tests with `monkeypatch.delenv(...)` so a
   stray parent-process env can't false-pass them again.

0.3.4 → 0.3.5. Verifies on the next CI run.
