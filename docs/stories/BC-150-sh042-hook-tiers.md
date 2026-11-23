---
id: BC-150
title: hook-commit + hook-push fleet tiers (SH-042 mirror)
status: shipped
created: 2026-11-23
---

# BC-150 — `hook-commit` + `hook-push` fleet tiers

## Who + when

Agents and humans running the pre-commit / pre-push gates on beaconry. Mirrors shepherd SH-042: both tier *names* become canonical across the fleet so no one has to memorise per-repo incantations (`make check-fast` on beaconry, `make check` on HRC, `make check-smart` on forge…).

## What

Add two thin Makefile wrappers:

- `hook-commit` → `COMMIT_STEPS = lint type` (~15 s budget)
- `hook-push`   → `PUSH_STEPS = check-fast verify-version-bumped verify-changelog verify-feature-complete` (~2 min budget)

`check-fast` lands as a forward-compat alias (`check-fast: check`). SH-023 / BC-149 will later tighten it to a pytest-xdist subset without touching this wrapper.

## Why

- **Fleet contract.** Shepherd and six managed repos share the tier names; the step contents stay repo-native.
- **Pre-push gate rewires painlessly.** The existing husky/pre-commit-config hooks that today call `make check` or `make check-fast` can re-point at `make hook-push` once every fleet repo exposes it (follow-up).

## Acceptance

See `poc/tests/integration/test_BC_150_hook_tiers.py` (5 assertions):

1. `.PHONY: hook-commit` declared
2. `.PHONY: hook-push` declared
3. `make -n hook-commit` emits lint/type markers
4. `make -n hook-push` emits each verify-\* marker
5. `check-fast` forward-compat alias target exists

## Capability

`CAP-S008` (new; `docs/specs/CAPABILITIES.md`) — fleet-tier hook-commit + hook-push wrappers.
