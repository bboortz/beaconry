---
id: BC-145
title: Shepherd Release 0 POC (second pilot after healthy)
status: in-progress
created: 2026-11-21
branch: feat/BC-145-shepherd-poc
plan: docs/plan/BC-145-shepherd-poc-plan.md
---

# BC-145: Shepherd Release 0 POC on beaconry

Companion to [`bboortz/shepherd SH-004`](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-004-r0-poc-on-healthy.md) and [`bboortz/healthy HC-008`](https://github.com/bboortz/healthy/blob/main/docs/stories/HC-008-shepherd-poc.md). beaconry becomes the **second pilot** of the Shepherd R0 POC. Scope mirrors HC-008 — bespoke per-repo workflow, blocklist + diff-size validator, no sandbox perimeter yet (R1 scope).

## Situation

- healthy is the first R0 pilot; its first dispatch ran green against zero findings on 2026-11-21.
- `healthy` is a single Zig file with no third-party deps, so Trivy had nothing to flag — the live pipeline worked but did not exercise the agent path.
- beaconry has a real Python + Docker dependency surface (`pyproject.toml`, multi-image Compose, Dockerfiles), so Trivy will have material to find and the agent's "deps-only, scope level 1" behaviour has a realistic test bed.

## Goal

Land the shepherd-poc workflow on beaconry with the same safety envelope as healthy. Prove the pipeline works against a non-trivial dependency surface. Keep the known R0 gap (no network perimeter) explicit.

## Acceptance

Mechanically checkable (no network required):

- `.github/workflows/shepherd-poc.yml` exists with a cron trigger and `workflow_dispatch`.
- All third-party `uses:` lines in that workflow are 40-hex SHA pins.
- No workflow in `.github/workflows/` uses `aquasecurity/trivy-action`.
- `scripts/shepherd-poc/validate.sh` exists, is executable, and refuses diffs that touch the protected-files blocklist or exceed 50 changed lines.
- `scripts/shepherd-poc/install-trivy.sh --dry-run` prints a `cosign verify-blob --new-bundle-format` command against the aquasecurity OIDC identity.
- `poc/tests/integration/test_BC_145_shepherd_poc.py` passes 100% without network (`pytest -k BC_145`).
- `make check-full` stays green on `dev`.

Runtime-only (after `ANTHROPIC_API_KEY` is set on beaconry and the first run completes):

- `trivy fs --severity HIGH,CRITICAL --ignore-unfixed` reports findings or exits cleanly with none.
- If findings exist, the agent produces either a diff ≤ 50 lines that `validate.sh` accepts, or `NO_FIX_POSSIBLE`.
- Any diff that would touch `.github/workflows/`, `.shepherd.yml`, `VERSION`, `*.env`, `*.pem`, or `*.key` is blocked by `validate.sh` with exit 2.

## Out of scope

- Config file (`.shepherd.yml`) — R1.
- Auto-merge — R1. Every PR this workflow opens is reviewed by hand.
- Waiting period from release date — R1.
- Network perimeter on the agent step — R1 (documented gap).
- ntfy / Telegram notifications — R1+.
- Any beaconry-specific guardrails (e.g. "shepherd must not touch perf-baseline.json") — add as follow-up BC-NNN if drift observed.

## CAPs delivered

- `shepherd.scan-trivy` — Trivy fs scan integrated into beaconry's scheduled CI.
- `shepherd.sandbox.no-secrets` — agent step receives only `ANTHROPIC_API_KEY`; no `GITHUB_TOKEN`.
- `shepherd.sandbox.diff-only` — agent writes a diff; separate trusted step applies it.
- `shepherd.blocklist.protected-files` — enforced by `scripts/shepherd-poc/validate.sh`.
- `shepherd.gate.diff-size` — 50-line cap in `validate.sh`.
- `shepherd.scope.level-1` — prompt binds the agent to deps + lockfiles.
- `supply-chain.sha-pin-all-actions` — enforced by the acceptance test on the new workflow.

See [`docs/plan/BC-145-shepherd-poc-plan.md`](../plan/BC-145-shepherd-poc-plan.md).
