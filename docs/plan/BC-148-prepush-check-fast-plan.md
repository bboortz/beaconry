# BC-148 — Swap pre-push hook from check-full to check

*Branch: `feat/SH-022-prepush-check-fast`*
*Start date: 2026-11-22*
*Story: `docs/stories/BC-148-prepush-check-fast.md`*

## Situation

Pre-push hook `make-check-full-pre-push` runs `make check-full` which includes Docker-based compose + real_tools tests. On 2026-11-22 (during the SH-029 fleet convergence), those tests hit Docker Hub rate-limit 429 repeatedly — same root cause @benni already fixed on forge (nightly build retries). On beaconry the failure forced routine `--no-verify` pushes.

## Problem

- Pre-push hook can take 3+ min on a good day, much longer on Hub-rate-limit days.
- `--no-verify` becomes the default, defeating the hook's purpose.
- CI still runs check-full, so skipping it on pre-push is not a safety regression — just a locality shift.

## Goals

1. `make check` (not `make check-full`) runs on pre-push.
2. `make check-full` still invoked somewhere (CI / nightly) — safety net preserved.
3. Pre-push wall-clock drops to ≤ 60s typical.

## Scope

Files changed:

- `.pre-commit-config.yaml` — hook id + entry command (2 lines) + explanatory comment.

No changes to Makefile targets; existing `check` and `check-full` definitions unchanged.

## Design delta

- Hook id renamed `make-check-full-pre-push` → `make-check-pre-push` (so pre-commit framework re-registers cleanly; old id orphans gracefully on next `pre-commit install`).
- Entry command drops `BEACONRY_SKIP_CLI_SMOKE=1` + `check-full` → `make check`.
- `check` already excludes CLI-smoke (it's only in test-cli which check-full doesn't call either).

## Alternatives rejected

- **Drop pre-push entirely** — loses the cheap fast-tier gate. Keep the hook, just make it fast.
- **Make check-full retry on Hub rate limits** — fixes the symptom, not the "pre-push is slow" root. Add in a separate PR if desired.
- **Narrow check-full by removing compose** — breaks check-full's intent (full container verification). check is the right fast tier.

## Breaking changes

- Local `git push` without `--no-verify` now runs `make check` instead of `make check-full`. Behaviour change for anyone who relies on pre-push catching compose/real_tools regressions. CI covers it; we accept the trade-off.

## Testing strategy

- Push this branch; confirm hook runs `make check` and not `make check-full`.
- Confirm the full-tier runs on CI (shepherd-gates.yml + any nightly).

## Testing commands

```sh
pre-commit install --hook-type pre-push   # re-register hook id
make check                                # run manually to see wall-clock
```

## Rollout

Merge to dev. Operators who already have the hook installed: run `pre-commit install --hook-type pre-push` on their next pull (safe to do anytime).

## Shipping evidence

| Item | Pointer | Status |
|------|---------|--------|
| `.pre-commit-config.yaml` points pre-push at `make check` | file | ✅ |
| Old `make-check-full-pre-push` hook id removed | file | ✅ (renamed to `make-check-pre-push`) |
| CI runs check-full on dev push | existing shepherd-gates.yml / CI | ✅ no change |
| Pre-push wall-clock ≤ 60s | observable after merge; measure on first few post-merge pushes | ⚠️ (deferred) |
