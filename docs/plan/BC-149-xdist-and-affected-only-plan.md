# BC-149 — pytest-xdist + real test-affected scoping

*Branch: `feat/BC-149-xdist-and-affected-only`*
*Start date: 2026-11-22*
*Story: `docs/stories/BC-149-xdist-and-affected-only.md`*

## Situation

Shepherd's SH-023 story prescribes beaconry xdist + affected-only. User reprioritized 2026-11-22 ("accelerate normal development; beaconry hurt in the past most") → combine both wins in one PR.

Current state: 1714 tests serial w/ coverage, PR median 4.0 min (SH-026 baseline). `make test-affected` is identity → `make test`.

## Problems

1. Full suite parallelism unused (single-threaded pytest).
2. `make test-affected` runs everything regardless of scope.
3. No cheap "I only touched hub" dev-loop (3-min wall every iteration).

## Goals

1. `pytest-xdist` added to `requirements-dev.txt`; `addopts` enables `-n auto --dist loadgroup`.
2. `scripts/test-updates.sh` scopes pytest by changed-module mapping (homelab-pattern).
3. `make test-affected` → `make test-updates` → `scripts/test-updates.sh`.
4. Full-run bailout on tooling/config/conftest/workflow touches.
5. Global tests (traceability + docs-drift + evidence-pointers) always run.

## Scope

- `requirements-dev.txt` — add `pytest-xdist==3.8.0`.
- `pyproject.toml` — `addopts` grows `-n auto --dist loadgroup`.
- `Makefile` — `test-affected` + new `test-updates` target + help line.
- `scripts/test-updates.sh` (new, ~130 lines bash) — module→tests mapping with dry-run mode.

No changes to: existing `check` / `check-full` body; no test file edits; no pytest marker changes; no coverage threshold change.

## Alternatives rejected

- `pytest-testmon` — auto change-impact via pytest graph. Adds runtime dep + .testmondata file to manage. Chose explicit module→test map for transparency; mirrors homelab.
- `pytest --last-failed` only — great for local iteration, weak as a PR gate signal.
- Mark every test with its module via custom markers — invasive refactor; path-based mapping is cheaper.
- Full-affected-only for `make test` (not just `test-affected`) — breaks the SH-026 baseline + CI muscle memory. `test` stays full; scoping is opt-in via `test-affected`.

## Breaking changes

- `make test` now runs in parallel (`-n auto`). Tests relying on process-wide shared state (e.g. singleton global mutable module) could fail. Mitigation: `--dist loadgroup` keeps `xdist_group`-marked tests serial; existing compose/e2e tests deselect-by-default stay as-is.

## Testing strategy

- Dry-run on this branch (no code change) → defensive full-run path.
- Simulated hub-only change (local temp commit) → scoped hub test paths + 3 globals.
- Simulated tooling touch (pyproject.toml) → full-run bailout.
- CI runs full `check-full` (which includes `test`) → verifies xdist doesn't break any existing test.

## Testing commands

```sh
bash scripts/test-updates.sh --dry-run           # inspect dispatch
pytest -n auto poc/tests/unit/ --no-cov          # verify xdist works
```

## Rollout

Merge to dev. First PR after merge observes the delta. If a test fails under parallelism, mark it `@pytest.mark.xdist_group(name="X")` to serialize with its peers; do NOT disable xdist.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|------|----------|---------|--------|
| pytest-xdist in requirements-dev.txt | integration | `test_BC_149_xdist_affected.py::test_pytest_xdist_in_requirements` | ✓ |
| pyproject.toml addopts enables -n auto | integration | `test_BC_149_xdist_affected.py::test_pyproject_addopts_enables_xdist` | ✓ |
| scripts/test-updates.sh exists + executable | integration | `test_BC_149_xdist_affected.py::test_test_updates_script_exists` | ✓ |
| test-updates.sh dry-run scopes to touched module | integration | `test_BC_149_xdist_affected.py::test_dry_run_scopes_to_module` | ✓ |
| tooling touch triggers full-run bailout | integration | `test_BC_149_xdist_affected.py::test_tooling_touch_triggers_full_run` | ✓ |
| Makefile test-affected → test-updates | integration | `test_BC_149_xdist_affected.py::test_makefile_test_affected_is_test_updates` | ✓ |
| Makefile help advertises scoped behaviour (discoverability) | integration | `test_BC_149_xdist_affected.py::test_help_mentions_scoped_test_affected` | ✓ |
