# BC-145-R1 — test rewrite + unit-coverage fill

*Branch: `fix/bc-145-shepherd-poc-r1`*
*Start date: 2026-11-22*
*Story: `docs/stories/BC-145-shepherd-poc.md`*

---

## Situation

Two pre-existing CI gates failed on the dev-targeting PR that migrated beaconry's shepherd caller from the R0 inline-script pattern (`shepherd-poc.yml`) to the SH-007 reusable-workflow pattern (`shepherd.yml` + `shepherd-gates.yml`):

1. **`poc/tests/integration/test_BC_145_shepherd_poc.py`** still asserted the R0 shape (file paths, inline steps, scripts/shepherd-poc/*). Main-branch CI doesn't run `make check-full` so the drift was invisible; dev CI runs it and failed.
2. **Coverage threshold.** Main dropped below the 85% floor earlier today — latent, because the broken test above exited before coverage was computed. Surfaced here as a secondary failure.

## Delta

**Modified**

- `poc/tests/integration/test_BC_145_shepherd_poc.py` — rewrite the R0 assertions to the SH-007 caller-wiring contract (both workflow files exist, pin reusable workflow at an SHA, cron + workflow_dispatch, no third-party Trivy wrapper, every uses: 40-hex SHA-pinned). Drop the sandbox / scripts / scope-level-1 assertions — those invariants now ride inside bboortz/shepherd's own suite.
- `docs/plan/BC-145-shepherd-poc-plan.md` — sync the shipping-evidence table with the R1 test surface.
- `docs/CAPABILITIES.md` — flip CAP-S002..CAP-S006 to `status: aspirational` (the invariants are honoured by shepherd's reusable workflow but no longer verified on beaconry).

**Added**

- `poc/tests/unit/test_dogfood_scan.py` — parse_findings + main() + _resolve_docker_gid (lifts dogfood_scan.py 43% → 94%).
- `poc/tests/unit/test_verify_base_images.py` — _split_ref + cache roundtrip + check() + main() (lifts 54% → 76%).
- Expanded coverage in `test_verify_evidence_pointers.py` (54% → 87%), `test_cli_smoke_scenarios.py` (59% → 70%), `test_BC_132_perf_gate.py` (perf_gate MemoryCheck + load_exceptions + is_active + perf_seeder.main).
- Expanded coverage in `test_compose_env_propagation.py` (57% → 92%).

## Rejected alternatives

| Alt | Why not |
|---|---|
| Keep the R0 assertions, add a feature flag | The R0 inline scripts and workflow are deleted — the assertions can't pass. |
| Lower `fail_under` from 85 to 84 | Pragmatic but hides the regression. Writing the missing unit tests is the clean fix per user direction. |
| Backfill `verified-by:` entries for CAP-S002..S006 with the *shepherd* test suite | Those tests don't live in this repo; per-repo traceability must point at per-repo tests. Marking aspirational is honest. |

## Rollout

- [ ] `make check-full` green locally.
- [ ] CI green on the PR.
- [ ] Merge → the main-branch Release workflow unblocks.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| BC-145 caller-wiring assertions pass | integration | `test_BC_145_shepherd_poc.py` | ✓ |
| dogfood-scan coverage ≥ 65% | unit | `test_dogfood_scan.py` | ✓ |
| verify-base-images coverage ≥ 65% | unit | `test_verify_base_images.py` | ✓ |
| verify-evidence-pointers coverage ≥ 65% | unit | `test_verify_evidence_pointers.py` | ✓ |
| compose-env coverage ≥ 65% | unit | `test_compose_env_propagation.py` | ✓ |
| cli-smoke coverage ≥ 65% | unit | `test_cli_smoke_scenarios.py` | ✓ |
| Global coverage ≥ 85% floor restored | gate | `make check-full` green | ⚠️ pending CI |
