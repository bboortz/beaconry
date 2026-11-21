# BC-145 — Shepherd Release 0 POC (second pilot after healthy)

*Branch: `feat/BC-145-shepherd-poc`*
*Start date: 2026-11-21*
*Story: `docs/stories/BC-145-shepherd-poc.md`*

---

## Situation

healthy's shepherd-poc (HC-008) is live and green. The R0 design (bboortz/shepherd SH-004) mandates "one repo, one file, one secret" — so beaconry gets its own copy of the workflow, scripts and acceptance test. R1 will converge all pilots on a shared reusable Action; for R0, duplication is the design.

## Delta

**New files**

- `.github/workflows/shepherd-poc.yml` — identical shape to healthy's, except `cron: "30 5 * * *"` (30-minute offset from healthy to avoid API-key-usage stampede and to make per-repo workflow runs easier to tell apart in logs).
- `scripts/shepherd-poc/install-trivy.sh` — identical to healthy's. Pins Trivy to v0.69.3, verifies with `cosign verify-blob --new-bundle-format`.
- `scripts/shepherd-poc/agent-call.sh` — identical to healthy's. `claude-sonnet-4-6`, `max_tokens=4096`, 3-retry loop, scope-level-1 prompt.
- `scripts/shepherd-poc/validate.sh` — identical to healthy's. Blocklist + 50-line cap.
- `scripts/shepherd-poc/open-pr.sh` — identical to healthy's. Branches off `dev`, labels `shepherd-poc` + `needs-review`.
- `scripts/shepherd-poc/README.md` — adapted to point at beaconry's story.
- `poc/tests/integration/test_BC_145_shepherd_poc.py` — pytest-style, 10+ assertions, no network. Mirrors HC-008's shell test using `subprocess`.
- `poc/tests/integration/fixtures/BC_145_*.patch` — four diff fixtures (blocked workflow, blocked `.shepherd.yml`, oversize, OK small — "small" adapted to `pyproject.toml`).

**Modified files**

- `docs/stories/INDEX.md` — add BC-145 row.
- `docs/CAPABILITIES.md` — add `shepherd.*` section (7 CAPs, status `in-progress` at R0 level).
- `CHANGELOG.md` — `[v0.22.19]` block under `[Unreleased]`.
- `VERSION` — `0.22.18` → `0.22.19`.

## Rejected alternatives

| Alt | Why not |
|---|---|
| Wait for R1 reusable Action in shepherd | SH-004 says do R0 in parallel on multiple repos to learn before packaging. One pilot (healthy) isn't enough signal. |
| Adapt validator to protect `perf-baseline.json` and `coverage.json` | Not needed at R0. Shepherd's scope level 1 is deps + lockfiles — it won't touch those naturally. Add if observed drift. |
| Port scripts to Python to match beaconry's house language | Scripts are repo-agnostic; keeping them identical to healthy's shell versions means one bug-fix place per script. |
| Run cron at same 05:00 UTC as healthy | Causes API-key stampede and makes per-repo runs harder to tell apart. Offset to 05:30. |

## Known gap (R0)

Same as HC-008: the agent step runs on a stock GitHub-hosted runner with normal egress. The validator is the only enforcement line. R1 adds network lockdown.

## Rollout

- [ ] `make check-full` green with the new files
- [ ] `pytest poc/tests/integration/test_BC_145_shepherd_poc.py` green
- [ ] PR body calls out the new `ANTHROPIC_API_KEY` secret + the sandbox gap
- [ ] After merge + release: `gh secret set ANTHROPIC_API_KEY -R bboortz/beaconry`
- [ ] First `workflow_dispatch` observed (expect either findings → PR or "no findings")
- [ ] `shepherd-managed` topic applied to the beaconry repo
- [ ] `docs/FLEET.md` in shepherd updated with beaconry row

## Shipping evidence

Mirrors HC-008's. Filled when the first live dispatch completes.
