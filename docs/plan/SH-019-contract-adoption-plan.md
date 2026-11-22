# SH-019 — Adopt shepherd dev-cycle contract (Makefile aliases)

*Branch: `feat/SH-019-contract-adoption`*
*Start date: 2026-11-22*
*Story: Tracked in shepherd — [SH-019](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-019-fleet-contract-rollout.md) under the [SH-014 umbrella](https://github.com/bboortz/shepherd/pull/23)*

---

## Situation

Beaconry's Makefile has `lint`, `test`, `smoke`, `check`, `format`, bump/perf/demo targets — but no `build`. Shepherd's SH-014 umbrella (2026-11-22) defines an eight-target `make` contract (`build`, `test`, `test-affected`, `smoke`, `release-artifacts`, `ci-branch`, `ci-patch`, `ci-release`, `ci-nightly`) that every managed repo exposes. The dev-cycle research brief specifically flagged beaconry's missing `build`: "Beaconry add `make build` (image or no-op)". Without it, shepherd's reusable workflow fails at the \"build\" step.

---

## Problems to solve

- `make build` is undefined → shepherd verification fails on the \"build\" step.
- No `test-affected` / `smoke` / `release-artifacts` / `ci-*` canonical targets.
- `shepherd-bump --verify-contract` (SH-017, pending in shepherd) will fail on beaconry without them.

---

## Goals

1. `make -n build` returns a sensible no-op (Python service — no compiled artifact).
2. `make -n test-affected`, `make -n smoke`, `make -n release-artifacts`, and `make -n ci-{branch,patch,release,nightly}` all resolve to real beaconry work.
3. Existing `lint`, `test`, `check`, `smoke`, `format`, `check-full` recipes byte-identical.

---

## Scope

Files changed:

- `Makefile` — 29 lines appended under a new `##@ Shepherd dev-cycle contract (SH-014 / SH-019)` section. New targets: `build`, `test-affected`, `release-artifacts`, `ci-branch`, `ci-patch`, `ci-release`, `ci-nightly`, `ci-nightly-body`. Zero edits to existing targets.

No changes to: existing `lint` / `test` / `smoke` / `check` / `format` recipes; workflow YAML; CI configuration; `.shepherd.yml`.

---

## Design delta

Contract tier bodies anchor on beaconry's native idioms. `test-affected` identity-aliases to `test` because beaconry has no affected-only mechanism today — SH-023 (shepherd) will later add `pytest-xdist` + `check-fast` split. Both `build` and `release-artifacts` are no-ops (beaconry is a Python service with no release artifact to promote across tiers).

---

## Alternatives rejected

- **`build: lint`** — misleading. The contract says build should "build the repo's artifact or run the lint equivalent on a clean runner". A no-op is cleaner for a Python service.
- **Add pytest-xdist and split `test-affected` in the same PR** — scope creep. SH-023 does that.
- **Rename `test` or `check`** — would break pre-push hooks, CI workflow references.

---

## Breaking changes

None. All new targets are additive. Existing `test`, `check`, `smoke`, `lint`, `format` keep working.

---

## Testing strategy

- `make -n` for each contract target produces a sensible plan (verified locally).
- Existing `make check` unchanged — the full gate still passes.

---

## Rollout

Merge → `main` → no production action required (Makefile-only, no deploy). Next PRs on this track:

- **SH-023** (shepherd): pytest-xdist adoption; splits `check` into `check-fast` + nightly.
- **SH-017** (shepherd): `shepherd-bump --verify-contract` passes on beaconry after this.

---

## Shipping evidence

| Item | Pointer | Status |
|------|---------|--------|
| `make -n build` resolves | `true` | ✅ |
| `make -n test-affected` resolves | `pytest` | ✅ |
| `make -n smoke` resolves | existing `smoke` target | ✅ |
| `make -n release-artifacts` resolves | `true` | ✅ |
| `make -n ci-branch` resolves | `lint` + `test` | ✅ |
| `make -n ci-patch` resolves | `smoke` body | ✅ |
| `make -n ci-release` resolves | echo note | ✅ |
| `make -n ci-nightly` resolves | `check` body | ✅ |
| Existing `check` behaviour unchanged | manual inspection | ✅ |
