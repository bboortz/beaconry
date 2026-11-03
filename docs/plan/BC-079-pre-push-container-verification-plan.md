# BC-079 — Pre-push runs full container verification

*Branch: `feat/BC-079-pre-push-container-verification`*
*Start: 2026-11-03*

## Why

BC-078 ship-verification caught two pre-existing silent-failure holes:

1. `test_hub_container_has_no_test_mode_code` scans hub.py source
   for `BEACONRY_TEST_MODE`. BC-038 re-added that string for the
   scenario harness. The test has been red on main since
   2026-10-31 — but swallowed.
2. `Makefile:268` has `-$(PYTHON) -m pytest` where the leading `-`
   makes Make ignore the exit code. test-e2e failures land green.

A push with a broken hub boot would reach `dev` with no gate
flagging it. BC-079 fixes both, then wires pre-push to run
`make check-full` so container regressions block pushes.

## Scope

### 1. Rewrite the stale test (`test_hub_container_has_no_test_mode_code`)

Was a source scan — easy to defeat, false-positive-prone. Replace
with a runtime probe: curl `GET /test/__driver__/snoozes` against
the test container → expect 404 because `BEACONRY_TEST_MODE` is
not set in `docker-compose.test.yml`. If the route is reachable,
the test code is live in prod → fail.

### 2. Fix the Makefile swallowing bug

`Makefile:test-e2e` line 268 drops the leading `-` on
`$(PYTHON) -m pytest`. `exit $$EXIT` already propagates — the
`-` was redundant and defeated the propagation.

Also audit the rest of the Makefile for the same anti-pattern
(`-$(MAKE)` / `-$(PYTHON)`). The `-` prefix on `make install-hooks`
and `make clean` is intentional (they're best-effort).

### 3. Wire pre-push to `make check-full`

Edit `.pre-commit-config.yaml`:

- Delete `pytest-pre-push` (make check-full includes pytest).
- Add `make-check-full-pre-push`:
  ```yaml
  - id: make-check-full-pre-push
    name: make check-full (pre-push)
    entry: bash -c 'BEACONRY_SKIP_CLI_SMOKE=1 make check-full'
    language: system
    pass_filenames: false
    stages: [pre-push]
  ```
- Keep `verify-version-bumped`, `verify-changelog`,
  `verify-feature-complete` (not in `make check-full`).

Pre-push time: ~45s (fast tier) + ~75s (compose + real_tools) +
~20s (e2e) + ~15s (scenarios) + 0s (CLI smoke, skipped) = ~2:35.
Acceptable for the "I'm about to push to dev" blast radius.

### Breaking changes

Yes, developer-visible:

1. `git push` now takes ~2:35 locally instead of ~45s. Only applies
   to pushes, not commits. Bypass with `--no-verify` only in
   emergencies.
2. `make check-full` must succeed to push — fails on any compose /
   e2e / scenario regression.
3. Developers without Docker running locally will see pushes fail.
   Not a new constraint — the compose + real_tools tier already
   needs Docker — but it hits one tier earlier now.

### Not addressed

- Caching: skip the full check if poc/ + pyproject.toml haven't
  changed since the last successful run. Future ergonomic win.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Runtime probe replaces source-scan test | e2e | `test_BC_011_probe_flow.py::test_hub_container_has_no_test_mode_code` (rewritten) | ✓ |
| test-e2e failures propagate exit code | integration | `test_BC_079_pre_push_container_verification.py::test_test_e2e_does_not_swallow_failures` | ✓ |
| Pre-push config invokes make check-full | integration | `test_BC_079_pre_push_container_verification.py::test_pre_push_hook_runs_make_check_full` | ✓ |
| Pre-push config sets BEACONRY_SKIP_CLI_SMOKE | integration | `test_BC_079_pre_push_container_verification.py::test_pre_push_skips_cli_smoke` | ✓ |
| Pre-push config removes standalone pytest hook | integration | `test_BC_079_pre_push_container_verification.py::test_pre_push_no_standalone_pytest` | ✓ |
| No swallowing `-` prefix in check/test-* recipes | integration | `test_BC_079_pre_push_container_verification.py::test_no_dash_prefix_on_check_subcommands` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Version

0.7.0 → 0.8.0 (minor — new blocking gate changes developer workflow).
