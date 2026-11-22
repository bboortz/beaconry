# SH-029 — Adopt feat/* → dev → main convergence (default branch = dev)

*Branch: `feat/SH-029-branching-convergence`*
*Start date: 2026-11-22*
*Story: Tracked in shepherd — [SH-029](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-029-fleet-branching-convergence.md) under the [SH-014 umbrella](https://github.com/bboortz/shepherd/pull/23)*

---

## Situation

Beaconry already has a `dev` branch and uses it as the integration branch. GitHub's **default branch is still `main`** — a leftover from pre-dev-flow days. The shepherd SH-029 umbrella prescribes `default = dev` across the fleet for uniform tooling (`gh pr create` targets the right branch; CI triggers fire against the development tip).

Beaconry's release flow today is the `bump-*` family + CD pipeline — predates SH-029's shared `ci-release-promote` target. This PR adds the shared `ci-release-promote` alongside (not instead of) the existing release flow. Operator keeps using `make bump-*` or dispatches `ci-release.yml` — both work.

---

## Problems to solve

- `gh pr create` on beaconry currently targets `main` (wrong — PRs should land on `dev`).
- No shared FF-promote path for `dev → main` + tag. The existing `bump-*` flow does its own thing.
- `shepherd-bump --verify-contract` (SH-017, pending) expects `ci-release-promote` to exist.

---

## Goals

1. `.github/workflows/ci-release.yml` exists; dispatchable; runs `make ci-release`.
2. `make ci-release` calls a new `ci-release-promote` target that FFs `main` to `dev` and tags from `VERSION`.
3. Post-merge: GitHub default branch flipped `main → dev` via `gh repo edit`.
4. `gh pr create` (no explicit base) targets `dev`.

---

## Scope

Files changed:

- `Makefile` — add `ci-release-promote` target (~15 lines); replace placeholder `ci-release` body with `: ci-release-promote` dependency + keep a human-readable note about the existing bump-* flow.
- `.github/workflows/ci-release.yml` — new, 33 lines. Copied from shepherd's template (SHA-pinned actions, `ref: dev` checkout, `git config` for FF push, `timeout-minutes: 15`).
- `docs/plan/SH-029-branching-convergence-plan.md` — this doc (required by pre-push hook).

No changes to: existing `lint`/`test`/`check`/`smoke`/`bump-*` recipes; `.shepherd.yml`; existing workflow YAMLs besides adding one new file.

---

## Design delta

Beaconry mirrors shepherd's SH-029 Phase 1 shape locally (since beaconry doesn't `include` a shared snippet). `ci-release` depends on `ci-release-promote`; the latter is the shared FF+tag logic. The existing bump-* release flow is preserved; operator can dispatch `ci-release.yml` OR continue using `make bump-patch`.

---

## Alternatives rejected

- **Replace `bump-*` with `ci-release-promote`** — scope creep; bump-* works and has history. Add ci-release-promote alongside; retire bump-* in a follow-up if desired.
- **Leave beaconry on default=main** — the umbrella question answered 2026-11-22 chose fleet-wide convergence. Default=dev everywhere.
- **Inline the FF logic in ci-release.yml workflow** — bypasses the contract. Shepherd's `ci-release-promote` is the shared pattern; beaconry mirrors it.

---

## Breaking changes

- `make ci-release` now MUTATES git state (FF main to dev, tag, push) when run on `dev`. Previously printed a placeholder note. Impact: interactive runs on `dev` would actually promote; on any other branch it exits 1 safely.
- Post-merge: GitHub default branch flips to `dev`. Existing open PRs targeting `main` need base-changed to `dev` (or merged first).

---

## Testing strategy

- `make -n ci-release` resolves to the promote path.
- `make -n ci-release-promote` on this feat branch exits 1 ("must run on 'dev'") — safe-by-construction.
- Post-merge: operator dispatches `ci-release.yml` once as a no-op smoke; verify main FF'd to dev + `vX.Y.Z` tag created.

---

## Testing commands

```sh
make -n ci-release           # → ci-release-promote body
make -n ci-release-promote   # → shell assert + FF + tag + push
make verify-traceability     # → pass
```

---

## Rollout

Merge to `main` (current default). Then operator:

```sh
gh repo edit bboortz/beaconry --default-branch dev
# verify dev ruleset includes required checks + non-fast-forward rule
# verify main ruleset includes update restriction (FF-only) + admin bypass
gh workflow run ci-release.yml -R bboortz/beaconry --ref dev   # smoke
```
