---
id: BC-148
title: Swap pre-push hook from check-full to check (fast tier)
status: ready
created: 2026-11-22
---

# BC-148 — Swap pre-push hook from check-full to check

## Who + when

Every beaconry contributor running `git push`. Current pre-push runs `make check-full` which includes compose + real_tools + test-e2e + test-scenarios (Docker-based). Observed Docker Hub rate-limit failures 2026-11-22 during back-to-back pushes.

## What

`.pre-commit-config.yaml` stage `[pre-push]` hook `make-check-full-pre-push` → `make-check-pre-push`. Runs `make check` (fast tier: lint + types + unit + 15+ verify-* + smoke, ~45s typical) instead of `make check-full` (~3 min + Docker Hub dependency).

The full tier stays live in CI (shepherd-gates.yml + any nightly). Pre-push becomes the "fast guard" tier.

## Success

- `.pre-commit-config.yaml` references `make check` (not `make check-full`) in the pre-push stage.
- Developer `git push` without `--no-verify` completes in ≤ 60s typical (down from ~3 min + rate-limit variability).
- CI still runs `check-full` on dev push.

## Urgency

Quality-of-life. `--no-verify` was becoming routine on beaconry PRs during the SH-029 convergence (user observation 2026-11-22). Pre-push should be fast enough that the hook stays on by default.

## Gherkin

```gherkin
Scenario: pre-push hook runs the fast tier
  Given .pre-commit-config.yaml at HEAD
  Then  the pre-push stage runs `make check` (not `make check-full`)
  And   the hook id is `make-check-pre-push`

Scenario: full tier still covered elsewhere
  Then  `make check-full` is still invoked by CI workflows
  And   no acceptance test expects pre-push to run check-full
```

## Out of scope

- Removing `check-full` entirely (CI keeps it; useful as a bigger pre-release gate).
- Parallelizing pytest (BC-ish, separate story — shepherd's SH-023 scope).
- Changing the verify-version-bumped / verify-changelog / verify-feature-complete hooks (orthogonal, keep).

## CAPs

- EXT `shepherd.dev-cycle-cheap-pre-push` — beaconry's pre-push aligns with shepherd's dev-cycle doc rule: "pre-push should be fast enough that developers don't reach for --no-verify routinely."
