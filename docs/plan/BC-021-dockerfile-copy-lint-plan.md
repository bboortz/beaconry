# BC-021 — Dockerfile sources lint

*Branch: `feat/BC-021-dockerfile-copy-lint`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-021-dockerfile-copy-lint.md`*

---

## Situation

BC-012 added `verify-dockerfile-health-copy` to catch "service imports `beaconry_health` but Dockerfile forgot `COPY beaconry_health/`." That lint has a narrow policy — one external shared package. BC-020 landed `poc/hub/kev.py` + used `poc/hub/urgency.py` as a new import; both were missing from the hub Dockerfile. `make check` was green; only `make test-e2e` surfaced the `ModuleNotFoundError` after a full container rebuild. The regression class is identical to BC-012's — the lint policy just didn't cover intra-service .py files.

## Problems to solve

- `make check` can't catch "new .py added to a service but Dockerfile COPY line not updated."
- The failure mode is runtime-only: container boot dies, visible only in `make test-e2e` or production.
- Follows a known pattern (BC-012) that already exists for a different file class — natural generalization.

## Goals

1. New `make verify-dockerfile-sources` lint. (All Gherkin scenarios)
2. Wired into `make check` fast tier.
3. Retroactive run against `poc/hub/`, `poc/scout/`, `poc/telegram_mock/` is silent (Scenario 6).
4. Supports three COPY syntaxes: individual (`COPY hub/a.py hub/b.py ./`), glob (`COPY hub/*.py ./`), and directory (`COPY hub/ ./`).
5. Skips `__init__.py` and `test_*.py` (not entrypoint code).

## Scope

- **New:** `poc/scripts/verify_dockerfile_sources.py` — the lint
- **New:** `poc/tests/integration/test_BC_021_dockerfile_sources.py` — integration test (6 Gherkin scenarios)
- **New:** `poc/tests/unit/test_verify_dockerfile_sources.py` — unit tests for the three COPY matchers
- **Modified:** `Makefile` — add target + help + `check` wiring
- **Modified:** `memory/workflow.md § Blocking gates` — add row
- **Modified:** `docs/CAPABILITIES.md` — register CAP-T061
- **Modified:** `docs/stories/INDEX.md` — BC-021 row

## Non-goals

- No auto-fix.
- No stale-COPY detection (reverse direction).
- No general import-graph analysis — "every .py must be COPY'd" is simpler and covers the regression surface.
- Not expanding BC-012's lint — the two policies are distinct enough that merging adds branches without saving code.

## Design decisions

- **Policy: every non-test, non-`__init__.py` file in `poc/<service>/` must be matched by at least one COPY line.** Over-matching is fine; the cost of COPY-ing an unused .py is negligible.
- **COPY matching:** three variants supported:
  1. Explicit filename: `COPY hub/kev.py ...` → matches `kev.py` only.
  2. Glob: `COPY hub/*.py ...` → matches every `*.py` in `hub/`.
  3. Directory: `COPY hub/ ...` or `COPY hub ...` → matches everything in `hub/`.
- **Skipped files:** `__init__.py` (package marker, often not needed in container), `test_*.py` (not entrypoint code), `_private_*.py` by convention. Allowlist is small and explicit; no annotation syntax needed.
- **Service discovery:** every subdir of `poc/` that contains a `Dockerfile`. Shared-package dirs without Dockerfile (`beaconry_health/`, `scripts/`, `tests/`) are automatically skipped.
- **Error message:** names the Dockerfile, the service dir, and the specific files not covered — copy-pasteable fix hint.
- **No `--fix` flag.** Lint fails; dev edits the Dockerfile. Mechanical fix wouldn't know whether to add to an existing line or create a new one.

## Breaking changes

None. Additive gate.

## Merge plan

1. Branch `feat/BC-021-dockerfile-copy-lint`
2. Red integration test → red
3. Impl: `verify_dockerfile_sources.py`
4. Unit tests for the three COPY matchers
5. Register CAP-T061; INDEX; workflow.md gate table
6. `make check` green (includes retroactive run)
7. Fast-forward to `dev`

## Rollout plan

Additive. First `make check` run after merge will fail if any service's `*.py` file isn't COPY'd — but all three current services (hub, scout, telegram_mock) pass as of this branch's base.

## Shipping evidence

Pre-ship audit (DD-TGD step 6a). Story may only move `shipped` when every row is ✓.

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1 — uncovered .py fails the lint | integration test | `test_BC_021_dockerfile_sources.py::test_uncovered_py_file_fails` | ✓ |
| Scenario 2 — all covered passes | integration test | `test_BC_021_dockerfile_sources.py::test_all_covered_passes` | ✓ |
| Scenario 3 — `__init__.py` + `test_*.py` are skipped | integration test | `test_BC_021_dockerfile_sources.py::test_init_and_test_files_skipped` | ✓ |
| Scenario 4 — service dir without Dockerfile is skipped | integration test | `test_BC_021_dockerfile_sources.py::test_no_dockerfile_skipped` | ✓ |
| Scenario 5 — glob COPY `*.py` matches all | integration test | `test_BC_021_dockerfile_sources.py::test_glob_copy_matches_all` | ✓ |
| Scenario 6 — retroactive run against real repo clean | integration test | `test_BC_021_dockerfile_sources.py::test_retroactive_real_repo_clean` | ✓ |
| COPY-line parser: explicit filenames | unit test | `test_verify_dockerfile_sources.py::test_explicit_copy_matcher` | ✓ |
| COPY-line parser: glob | unit test | `test_verify_dockerfile_sources.py::test_glob_copy_matcher` | ✓ |
| COPY-line parser: dir | unit test | `test_verify_dockerfile_sources.py::test_dir_copy_matcher` | ✓ |
| CAP-T061 registered + bidirectional | verify-traceability | CAPABILITIES.md diff | ✓ |
| Makefile target + `make check` wiring + help text | lint diff | `grep verify-dockerfile-sources Makefile` | ✓ |
| `memory/workflow.md § Blocking gates` includes new row | docs | diff | ✓ |
| make check green (includes BC-019 + this new gate running retroactively) | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open.

## Testing strategy

- **Integration test** encodes all 6 Gherkin scenarios using tmp_path fixtures to build fake service dirs + Dockerfiles.
- **Unit tests** exercise the three COPY matcher functions in isolation.
- **Retroactive** against the real poc/ is a scenario 6 integration test — not a one-off manual check.

## Testing commands

```bash
source .venv/bin/activate
pytest poc/tests/unit/test_verify_dockerfile_sources.py -v --no-cov
pytest poc/tests/integration/test_BC_021_dockerfile_sources.py -v --no-cov
make verify-dockerfile-sources   # retroactive on real repo
make check
```
