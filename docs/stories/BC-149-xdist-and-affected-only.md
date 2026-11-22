---
id: BC-149
title: pytest-xdist + real test-affected scoping
status: ready
created: 2026-11-22
---

# BC-149 — pytest-xdist + real test-affected (SH-023 win)

## Who + when

Every contributor opening a PR to beaconry. Current `make test-affected` is an identity alias to `make test` → runs all 1714 tests. SH-026 baseline: 4.0 min PR median. User-flagged as painful (shepherd SH-022 priority review 2026-11-22).

## What

**Two complementary speedups**:

1. **pytest-xdist parallelism**: `-n auto --dist loadgroup` added to `pyproject.toml addopts`. Full-suite test run parallelises across all cores; `loadgroup` keeps tests marked with `xdist_group` together (shared DB / compose fixtures stay serial).

2. **Real test-affected via `scripts/test-updates.sh`**: git-diff → module → test-paths mapping. Mirrors homelab's pattern (by role) + forge's pattern (by image). Modules for beaconry: `hub`, `scout`, `scripts`, `beaconry_health`, `telegram_mock`, `tests`. Full-run bailout on tooling/config/conftest touches. Global tests (traceability, docs-drift, evidence-pointers) always run.

## Success

- `make test-affected` on a `poc/hub/` change runs only hub-adjacent tests + globals (not all 1714).
- `make test` parallelises (visible as `[gw0] … [gwN]` worker prefixes).
- Full-suite time drops from ~4 min → ≤ 2 min (xdist) → ≤ 1 min for most scoped PRs.
- Falls back to full on `Makefile`/`pyproject.toml`/`requirements*`/`conftest.py`/workflow-yml changes.

## Urgency

Quality-of-life — biggest fleet-side dev-cycle win left after SH-022. User explicitly called out beaconry alongside forge + homelab as "hurt in the past most".

## Gherkin

```gherkin
Scenario: hub-only change runs hub tests + globals only
  Given a PR touching only poc/hub/hub.py
  When  bash scripts/test-updates.sh --dry-run
  Then  the planned pytest invocation includes the hub test paths
  And   it does NOT include the full test suite
  And   it includes the 3 global invariant test files

Scenario: tooling touch triggers full-run bailout
  Given a PR touching pyproject.toml
  When  bash scripts/test-updates.sh --dry-run
  Then  the planned pytest invocation is the full suite (no file args)
  And   stdout mentions "full-run bailout"

Scenario: pytest-xdist is enabled on the full suite
  Given pyproject.toml at HEAD
  Then  addopts contains "-n" + "auto"
  And   addopts contains "--dist" + "loadgroup"

Scenario: pytest-xdist is listed in requirements-dev.txt
  Given requirements-dev.txt at HEAD
  Then  it contains "pytest-xdist=="
```

## Out of scope

- Caching stamp files (homelab has them; defer for beaconry until noise justifies).
- Moving existing @pytest.mark.compose / @pytest.mark.e2e to a shared `xdist_group` (orthogonal — those are already deselected by default via `-m "not compose…"`).
- Changing the fail_under=85% coverage floor (unchanged).

## CAPs

- EXT `shepherd.dev-cycle-cheap-pre-push` — beaconry's inner-loop speed now matches the SH-014 dev-cycle contract's spirit.
