---
id: BC-146
title: Fix Dockerfile-shape test drift (BC-070/082/083/097/099/100)
status: ready
created: 2026-11-21
---

# BC-146: Fix Dockerfile-shape test drift across BC-070/082/083/097/099/100

Follow-up filed during BC-145 (shepherd-poc). Running `make check-full` on `dev` surfaces 11 pre-existing test failures that are **unrelated to BC-145** — they are all **Dockerfile-shape expectations** that no longer match what `poc/hub/Dockerfile` and `poc/scout/Dockerfile` actually contain today.

## Observed on 2026-11-21 (dev HEAD)

| Test | Expected | Actual in Dockerfile |
|---|---|---|
| `test_BC_083_forge_image::test_hub_runtime_is_forge` | `FROM ghcr.io/bboortz/python-scratch-trixie:{latest,3.13} AS runtime` | `FROM runtime-src AS runtime` (composed from `base/python-trixie:latest AS runtime-src`) |
| `test_BC_083_forge_image::test_scout_runtime_is_forge` | same | same pattern |
| `test_BC_097_hc_healthcheck::test_runtime_from_tracks_latest` | latest-pinned runtime | now indirected through `runtime-src` |
| `test_BC_097_hc_healthcheck::test_verify_pinned_versions_passes_on_branch` | branch-local pinning check | drifted |
| `test_BC_099_base_abi_guard::test_runtime_src_stage_present` | checks for a specific `AS runtime-src` label | the label exists but the base image reference shape changed |
| `test_BC_100_telegram_mock_forge_base::test_runtime_src_stage_present` | same | same |
| `test_BC_082_homelab_deployment::test_pinned_versions_passes_live` | live homelab reachability | environment-dependent, not just Dockerfile |
| `test_BC_070_cli_install::test_pyproject_version_matches_version_file` | pyproject `version` matches `VERSION` | drift during version bumps (fixed by BC-145's `pyproject.toml` bump — can keep as regression guard) |

## Scope

- Reconcile each test's assertion with the current Dockerfile shape, OR update the Dockerfile to match the invariant the test encodes — whichever matches the design intent at the time the story originally shipped.
- Where the test depends on live infrastructure (BC-082 homelab reach, BC-083 GHCR image presence), add a proper `@pytest.mark.real_tools` / `@pytest.mark.compose` marker so the default profile (`-m "not real_tools and not compose and not e2e and not cold_start"`) deselects them cleanly.
- `make check-full` on a fresh dev should be 100% green at session end.

## Out of scope

- Any Dockerfile re-architecture. This story updates **tests**, not Dockerfiles.
- Fixing BC-145's shepherd-poc workflow (already green on its own suite).

## Acceptance

```gherkin
Scenario: make check-full is 100% green on dev
  Given the beaconry repo at HEAD of dev after this story ships
  When  `make check-full` is run
  Then  all pytest assertions pass
  And   no tests were quietly skipped unless marked real_tools / compose / e2e / cold_start

Scenario: SH-001 invariant still holds (no silent drift inside BC-146)
  Given this PR
  When  the acceptance for BC-145 is re-run
  Then  it still passes 18/18
```

## CAPs touched

None new. This is a regression-test-maintenance chore.

## Context

Filed by BC-145's push, which was bypassed once with `SKIP=make-check-full-pre-push` because the drift was pre-existing. BC-146 closes that loop.
