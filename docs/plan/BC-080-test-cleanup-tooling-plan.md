# BC-080 — `make cleanup` tooling

*Branch: `feat/BC-080-test-cleanup-tooling`*
*Start: 2026-11-03*

## Why

User observation: beaconry's own test runs leak CVE-affected
images onto the host (`redis:6.2.0`, `nginx:1.24`, `postgres:latest`,
`beaconry-hub:ci-test` etc). Scout picks them up on the next
cycle and the operator gets Telegram alerts for images nothing
on the fleet is actually running.

The previous mitigation was a CLAUDE.md paragraph + a
`.claude/commands/cleanup.md` slash command. Both are
instructions, not gates — they help Claude but not CI, not another
developer, and only when remembered. The right answer is tooling.

## Scope

### 1. `make cleanup`

New Makefile target. Delegates to `python -m poc.scripts.cleanup`
so the enumeration is Python-reviewable + unit-testable.

Cleanup steps (in order):

1. Tear down `beaconry-test` compose project (test-e2e stack).
2. Tear down `beaconry-scenarios` compose project.
3. Tear down `beaconry-test-ci` or any other known-ephemeral
   project (future-proofing).
4. Remove **test-only** images by exact name match:
   - `beaconry-hub:ci-test`
   - `beaconry-hub:scenarios`
   - `beaconry-test-telegram-mock:latest`
5. Remove **test-fixture** images that beaconry's own tests pull:
   - `nginx:1.24` (demo.sh, scenarios)
   - `nginx:1.24-fixture` (BC-002 real-pipeline fixture)
   - `nginx:alpine` (test-e2e probe target)
   - `redis:6.2.0`, `redis:7-alpine`, `redis:7` (scenario fixtures)
   - `postgres:latest`, `postgres:16-alpine`, `postgres:16`,
     `postgres:16.1.3-alpine` (scenario + BC-011 fixtures)
   - `nginxproxy/nginx-proxy:alpine` (BC-011 fixture)
   - `busybox:latest` (scenario fixture)
   - `alpine:main` (scenario fixture)
6. Report: N removed, M not present. Exit 0 always (idempotent).

Hard-coded safety list (never remove, even if the enumeration
grows later):
- `beaconry-hub:latest`
- `beaconry-scout:latest`
- `python:*` (base + builder stages)
- `gcr.io/distroless/*` (BC-078 runtime)

### 2. Auto-invoke on test exit

Three Makefile targets gain a trap. GNU make can't trap at the
recipe level — we need to shell-wrap. Pattern:

```makefile
test-e2e:
	@...
	$(PYTHON) -m pytest -m e2e poc/tests/e2e/ -v --no-cov; EXIT=$$?; \
	 echo "==> tearing down test stack"; \
	 cd poc && docker compose -p beaconry-test -f docker-compose.test.yml down -v; \
	 $(MAKE) --no-print-directory cleanup; \
	 exit $$EXIT
```

Same pattern for `test-scenarios` and `check-full`'s final step.

### 3. Shrink CLAUDE.md

The 33-line "container cleanup after testing" block becomes a
one-liner pointer to `make cleanup`. Delete the manual command
list — it's error-prone and gets stale.

Keep `.claude/commands/cleanup.md` but shrink it to a thin wrapper
that calls `make cleanup`.

### 4. Lint — test files that spin up compose must have teardown

Future-proof: a lint under `poc/scripts/verify_test_cleanup.py`
scans test files for `docker compose up` / `subprocess.run(["docker"...])`
patterns, checks for a corresponding `down` / `rm` in the same
file. (Deferred to BC-081 if BC-080 catches most of it.)

### Breaking changes

Minimal:
- `make test-e2e` now takes ~2s longer (image removal step).
- Developers who deliberately kept `nginx:1.24` cached locally for
  fast iterate-rerun cycles will see a 10-15s re-pull on the next
  run. Trade-off accepted per user: "remove everything the tests
  touched".

### Not addressed

- `SCOUT_EXCLUDE` defaults for test images in `docker-compose.yml`.
  User explicitly scoped BC-080 to the cleanup target only.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| `make cleanup` target exists in Makefile | integration | `test_BC_080_cleanup.py::test_makefile_has_cleanup_target` | ✓ |
| cleanup script removes test-only images | unit | `test_BC_080_cleanup.py::test_cleanup_removes_test_only_images` | ✓ |
| cleanup script preserves live-stack images | unit | `test_BC_080_cleanup.py::test_cleanup_preserves_live_stack_images` | ✓ |
| cleanup script removes test-fixture images | unit | `test_BC_080_cleanup.py::test_cleanup_removes_test_fixture_images` | ✓ |
| cleanup main() exits 0 on happy path | unit | `test_BC_080_cleanup.py::test_cleanup_main_exits_zero_on_success` | ✓ |
| cleanup handles missing docker gracefully | unit | `test_BC_080_cleanup.py::test_cleanup_handles_missing_docker_gracefully` | ✓ |
| cleanup is idempotent | unit | `test_BC_080_cleanup.py::test_cleanup_idempotent` | ✓ |
| test-e2e recipe auto-invokes cleanup | integration | `test_BC_080_cleanup.py::test_test_e2e_recipe_runs_cleanup_in_trap` | ✓ |
| test-scenarios recipe auto-invokes cleanup | integration | `test_BC_080_cleanup.py::test_test_scenarios_recipe_runs_cleanup` | ✓ |
| check-full recipe auto-invokes cleanup | integration | `test_BC_080_cleanup.py::test_check_full_recipe_runs_cleanup` | ✓ |
| CLAUDE.md shrunk — no manual command list | integration | `test_BC_080_cleanup.py::test_claude_md_points_at_make_cleanup` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Version

0.8.0 → 0.8.1 (patch — hardening + tooling, no new feature, no
schema or API change).
