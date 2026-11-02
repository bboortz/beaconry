# BC-071 — CI env leak: `BEACONRY_SKIP_VERSION_BUMP=1` broke bump-verify tests

*Branch: `fix/BC-071-ci-env-leak`*
*Start: 2026-11-02*

## Symptom

First real CI run on GitHub (BC-069's workflow) failed on `dev`:

```
FAILED test_verify_fails_when_version_unchanged
FAILED test_verify_fails_when_version_went_backwards
```

Local `make check` + `make check-full` had been green.

## Root cause

`.github/workflows/ci.yml` set `BEACONRY_SKIP_VERSION_BUMP: "1"` as
an env var on the `make check` step. The intent was "CI runs on
branches, pre-push gate shouldn't fire". But:

1. `make check` doesn't actually run `verify-version-bumped` —
   that lint lives only in the pre-push hook (`.pre-commit-config.yaml`).
   So the env was unnecessary.
2. The env leaked into every pytest subprocess spawned by `make
   check → pytest`. Two tests (`test_verify_fails_when_version_
   unchanged`, `test_verify_fails_when_version_went_backwards`)
   call `verify_version_bumped.check()` directly, which
   short-circuits to `[]` when the env is set. Expected `errors !=
   []`; got `[]`; test fails with `AssertionError: []`.

Same bug pattern bit the local push earlier — fixed there by
`env -u BEACONRY_SKIP_VERSION_BUMP` on the git-push command, but
CI didn't get the same wrapper.

## Fix

Two-line change:

1. `.github/workflows/ci.yml` — drop the `BEACONRY_SKIP_VERSION_BUMP`
   env from the `make check` step. Not needed; was defensive and
   bit back.
2. Both affected tests — add `monkeypatch.delenv("BEACONRY_SKIP_
   VERSION_BUMP", raising=False)` at the top. Hermetic by default so
   future env-leak class bugs can't silently false-pass them.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Hostile parent env no longer false-passes | unit | `test_bump_version.py::test_verify_fails_when_version_unchanged` | ✓ |
| Hostile parent env no longer false-passes (backwards) | unit | `test_bump_version.py::test_verify_fails_when_version_went_backwards` | ✓ |
| Hostile env simulation locally | manual smoke | `BEACONRY_SKIP_VERSION_BUMP=1 pytest …::test_verify_fails_when_version_unchanged` passes | ✓ |
| `make check` + `make check-full` green locally | multi-gate | logs | ✓ |

## Version

0.3.4 → 0.3.5 (patch, no runtime behaviour change).
