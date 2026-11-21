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

Mirrors HC-008's. Workflow-shape invariants are enforced at every `make check`
via the integration tests below; live-dispatch rows get filled after the first
`workflow_dispatch` run.

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Workflow file present at `.github/workflows/shepherd.yml` | integration | `test_BC_145_shepherd_poc.py::test_workflow_file_exists` | ✓ |
| Workflow has daily cron trigger | integration | `test_BC_145_shepherd_poc.py::test_workflow_has_cron` | ✓ |
| Workflow exposes `workflow_dispatch` | integration | `test_BC_145_shepherd_poc.py::test_workflow_has_workflow_dispatch` | ✓ |
| No third-party Trivy Action wrapper | integration | `test_BC_145_shepherd_poc.py::test_no_trivy_action_wrapper` | ✓ |
| Every `uses:` pinned to a commit SHA | integration | `test_BC_145_shepherd_poc.py::test_every_uses_is_sha_pinned` | ✓ |
| Shepherd scripts are executable | integration | `test_BC_145_shepherd_poc.py::test_script_is_executable[install-trivy.sh]` | ✓ |
| Shepherd scripts are executable (agent-call) | integration | `test_BC_145_shepherd_poc.py::test_script_is_executable[agent-call.sh]` | ✓ |
| Shepherd scripts are executable (validate) | integration | `test_BC_145_shepherd_poc.py::test_script_is_executable[validate.sh]` | ✓ |
| Shepherd scripts are executable (open-pr) | integration | `test_BC_145_shepherd_poc.py::test_script_is_executable[open-pr.sh]` | ✓ |
| Shepherd scripts are executable (regen-lockfile) | integration | `test_BC_145_shepherd_poc.py::test_script_is_executable[regen-lockfile.sh]` | ✓ |
| Validator blocks edits to `.github/workflows/*` | integration | `test_BC_145_shepherd_poc.py::test_validate_blocks_workflow_touch` | ✓ |
| Validator blocks edits to `shepherd.yml` | integration | `test_BC_145_shepherd_poc.py::test_validate_blocks_shepherd_yml_touch` | ✓ |
| Validator blocks oversize diffs | integration | `test_BC_145_shepherd_poc.py::test_validate_blocks_oversize_diff` | ✓ |
| Validator accepts a small allowed diff | integration | `test_BC_145_shepherd_poc.py::test_validate_accepts_small_allowed_diff` | ✓ |
| Trivy install uses `cosign verify-blob` | integration | `test_BC_145_shepherd_poc.py::test_install_trivy_dry_run_names_cosign_verify_blob` | ✓ |
| Agent prompt declares manifest-only scope (hybrid flow) | integration | `test_BC_145_shepherd_poc.py::test_prompt_declares_manifest_only_scope` | ✓ |
| Agent prompt names `shepherd.yml` as forbidden | integration | `test_BC_145_shepherd_poc.py::test_prompt_names_shepherd_yml_as_forbidden` | ✓ |
| Agent prompt specifies the "no fix possible" fallback | integration | `test_BC_145_shepherd_poc.py::test_prompt_specifies_no_fix_possible_fallback` | ✓ |
| Agent step env is only `ANTHROPIC_API_KEY` | integration | `test_BC_145_shepherd_poc.py::test_agent_step_env_only_has_anthropic_api_key` | ✓ |
| Workflow wires Setup Node.js step | integration | `test_BC_145_shepherd_poc.py::test_workflow_wires_setup_node_step` | ✓ |
| Workflow wires Regenerate lockfile step | integration | `test_BC_145_shepherd_poc.py::test_workflow_wires_regen_lockfile_step` | ✓ |
| Workflow wires second Validate (combined) step | integration | `test_BC_145_shepherd_poc.py::test_workflow_wires_second_validate_step` | ✓ |
| Live post-dispatch — first run on beaconry | live | first `workflow_dispatch` observed | ❌ pending |
