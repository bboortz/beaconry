# BC-082 — Homelab-deployment coverage

*Branch: `feat/BC-082-homelab-deployment-coverage`*
*Start: 2026-11-03*

## Why

BC-078/BC-079/BC-081 ship-verification kept finding gaps:
- Our distroless test suite greps Dockerfile strings; it doesn't
  boot the container.
- test-e2e + test-scenarios run with `BEACONRY_SKIP_GRYPE_DB=1`,
  so the grype DB download path is uncovered.
- No lint catches a pin regressing below the BC-081 floor.
- No gate catches beaconry's own tools shipping known-vulnerable
  embedded libraries (BC-081 was found by beaconry's own live
  stack alerting an operator over Telegram — about itself).

Operator's framing: *"ensure from the beaconry side that the
homelab deployment will never fail. If it fails, we need to learn
from it."*

## Scope

### 1. Pinned-version floor lint — `poc/scripts/verify_pinned_versions.py`

Fast-tier (`make check`) addition. Parses `poc/hub/Dockerfile`
and `poc/scout/Dockerfile`; asserts:

- `DOCKER_CLI_VERSION ≥ 29.9.2` (BC-081 floor — Go stdlib ≥1.26.9).
- Builder base matches `python:3.1[1-9]-slim-bookworm` or newer
  minor.
- Runtime stage is `gcr.io/distroless/python3-debian12:nonroot`
  exactly (any drift forces a conscious choice).

Error messages name the floor + the originating BC so operators
reading `make check` output can trace *why* the floor exists.

Wire into `make check` after `verify-dockerfile-sources`.

### 2. Dogfood grype gate — `poc/scripts/dogfood_scan.py` + `make dogfood-scan`

Full-tier addition. Pseudo-code:

```python
# 1. Rebuild images (compose up --build, no-op if already fresh)
# 2. For each of {beaconry-hub:latest, beaconry-scout:latest}:
#    - docker run --rm --group-add $DOCKER_GID \
#        -v /var/run/docker.sock:/var/run/docker.sock:ro \
#        --entrypoint /usr/bin/grype beaconry-hub:latest <target> -o json
#    - Parse JSON; collect findings where severity ∈ {High, Critical}
#      AND vulnerability.fix.state == "fixed"  (fixable)
#      AND vulnerability.fix.versions != []
# 3. Fail with per-finding breakdown if non-empty.
```

Escape hatch: `BEACONRY_SKIP_DOGFOOD=1` for dev-only emergencies.
Documented in VARIABLES.md as dev-only.

Wire into `make check-full` between `test-cli` and the final
`cleanup` step. The cleanup step is a no-op if dogfood already
succeeded; if it failed, cleanup still fires via the `exit $$EXIT`
pattern (BC-080).

### 3. Cold-start smoke test — `poc/docker-compose.cold-start.yml` + `test_BC_082_cold_start.py`

Full-tier addition under `@pytest.mark.compose`. Lifecycle:

1. `docker compose -p beaconry-cold-start -f poc/docker-compose.cold-start.yml up -d --build`
   — fresh project name, fresh volumes, random localhost ports
   (9100/9101 to avoid colliding with live stack on 8000/8001).
2. Poll `/readyz` on both services; max 180 s. First hub /readyz
   triggers grype DB download (~90 s on cold cache).
3. Assert all 8 endpoints = 200.
4. Assert hub `/healthz` has `grype_db: ok, grype_binary: ok, db: ok`.
5. Assert scout `/healthz` has `docker_socket: ok, images_seen: N`
   where `N > 0` (uses the host's own docker daemon via mounted
   socket; proof the socket + gid plumbing works).
6. Teardown via `exit $$EXIT` + `make cleanup`.

Compose file is intentionally minimal:
- Empty Telegram tokens (no outbound traffic).
- `MIN_CVSS=99` (findings never trigger notifications — this test
  gates boot, not alert quality).
- No `beaconry-data` volume reuse (`-v` on down + anonymous
  volume on up).

### 4. Learning record — append to `docs/plan/CI-FAILURES.md`

Entry for BC-081 — see story for the exact text.

### Breaking changes

Developer-visible but minor:
- `make check-full` adds ~2 min for cold-start + ~20 s for
  dogfood-grype. New pre-push time ~5 min (vs ~2:30 after BC-079).
- `BEACONRY_SKIP_DOGFOOD=1` becomes the dev-only emergency env.

### Not addressed

- Rolling pin updates (Dependabot for `DOCKER_CLI_VERSION`, base
  images). Future BC if dogfood gate starts firing weekly.
- CI-side mirror (ci.yml already runs `make check-full`).
- Cold-start test against a non-amd64 host (future, when we ship
  arm64 images).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Pinned-version lint catches old docker CLI pin | integration | `test_BC_082_homelab_deployment.py::test_pinned_versions_rejects_pre_bc081_docker_cli` | ✓ |
| Pinned-version lint catches old python minor | integration | `test_BC_082_homelab_deployment.py::test_pinned_versions_rejects_old_python_minor` | ✓ |
| Dogfood parser flags fixable Critical | integration | `test_BC_082_homelab_deployment.py::test_dogfood_parse_flags_fixable_critical` | ✓ |
| Dogfood parser tolerates unknown fix state | integration | `test_BC_082_homelab_deployment.py::test_dogfood_parse_tolerates_unknown_fix` | ✓ |
| Dogfood parser tolerates Medium severity | integration | `test_BC_082_homelab_deployment.py::test_dogfood_parse_tolerates_medium` | ✓ |
| Dogfood parser tolerates empty fix.versions | integration | `test_BC_082_homelab_deployment.py::test_dogfood_parse_tolerates_empty_fix_versions` | ✓ |
| Cold-start compose file exists on disk | integration | `test_BC_082_homelab_deployment.py::test_cold_start_compose_file_exists` | ✓ |
| Cold-start compose does not skip grype DB | integration | `test_BC_082_homelab_deployment.py::test_cold_start_compose_does_not_skip_grype_db` | ✓ |
| check-full invokes test-cold-start | integration | `test_BC_082_homelab_deployment.py::test_check_full_runs_test_cold_start` | ✓ |
| Cold-start all 8 endpoints green (hub+scout × 4) | compose | `test_BC_082_cold_start.py::test_cold_start_all_eight_endpoints` | ✓ |
| Cold-start scout /healthz shows docker_socket ok | compose | `test_BC_082_cold_start.py::test_cold_start_scout_healthz_shows_docker_socket_ok` | ✓ |
| Cold-start version strings match current VERSION | compose | `test_BC_082_cold_start.py::test_cold_start_version_matches_source` | ✓ |
| Pinned-version lint catches wrong distroless runtime | unit | `test_BC_082_homelab_deployment.py::test_pinned_versions_rejects_unknown_runtime` | ✓ |
| Pinned-version lint passes on shipped repo | unit | `test_BC_082_homelab_deployment.py::test_pinned_versions_passes_live` | ✓ |
| Pinned-version wired into `make check` | integration | `test_BC_082_homelab_deployment.py::test_make_check_runs_verify_pinned_versions` | ✓ |
| Dogfood scan parser flags High+ fixable | unit | `test_BC_082_homelab_deployment.py::test_dogfood_parse_flags_fixable_high` | ✓ |
| Dogfood scan parser tolerates won't-fix | unit | `test_BC_082_homelab_deployment.py::test_dogfood_parse_tolerates_wont_fix` | ✓ |
| Dogfood scan honors BEACONRY_SKIP_DOGFOOD | unit | `test_BC_082_homelab_deployment.py::test_dogfood_skip_env_opts_out` | ✓ |
| Dogfood wired into `make check-full` | integration | `test_BC_082_homelab_deployment.py::test_check_full_runs_dogfood_scan` | ✓ |
| Cold-start compose file exists + is minimal | unit | `test_BC_082_homelab_deployment.py::test_cold_start_compose_file_minimal` | ✓ |
| Cold-start test boots both services green within 180s | compose | `test_BC_082_cold_start.py::test_cold_start_all_eight_endpoints` | ✓ |
| Cold-start test confirms real grype DB download | compose | `test_BC_082_cold_start.py::test_cold_start_hub_healthz_shows_grype_db_ok` | ✓ |
| Cold-start test fires cleanup on teardown | integration | `test_BC_082_homelab_deployment.py::test_cold_start_recipe_runs_cleanup` | ✓ |
| CI-FAILURES.md has BC-081 entry | integration | `test_BC_082_homelab_deployment.py::test_ci_failures_log_has_bc_081_entry` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Version

0.8.2 → 0.9.0 (minor — new gate suite, developer-visible
pre-push time increase).
