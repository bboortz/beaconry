# BC-074 — Release automation + 24h soak + feature-completeness lint + CI-failure log

*Branch: `feat/BC-074-release-automation`*
*Start: 2026-11-02*

Operator asked for a streamlined and fully automated release cycle.
Four interlocking deliverables.

## 1. Release automation — gated promotion `release.yml`

New `.github/workflows/release.yml`, triggered only by
`workflow_dispatch` (manual button in Actions tab) — the review step
you asked to keep. When fired:

1. Checkout dev HEAD.
2. Assert CI's `Full check` has run green on dev's current HEAD
   (skip auto-promote if dev was pushed less than 2 min ago and CI
   hasn't completed yet).
3. Fast-forward main from dev.
4. Tag `v<VERSION>` where VERSION is `cat VERSION`.
5. Push `main` + tag.

CI (`ci.yml`) stays validation-only. The two workflows are
decoupled: operator clicks when ready; promotion runs automatically
and completely once clicked.

Why not fully-auto on push: your earlier CAP-T147 explicitly
reserved main as a manual gate, and your reply here confirmed
"behind a reviewable trigger". Workflow_dispatch is the minimal
button that satisfies both goals.

Auth: default `GITHUB_TOKEN` with `contents: write` permission
works unless main is a protected branch. If protection is added
later, we switch to a dedicated `GH_PAT` secret.

Also updates CAP-T147's description to reflect the new flow — no
longer "NO auto-merge dev → main"; now "manual trigger → full
automation downstream".

## 2. Soak window default 1h → 24h

`poc/scripts/soak.py::SOAK_WINDOW_S` default changes to 86400.
Operator can still override per-invocation (`SOAK_WINDOW_S=3600
make soak`). docs/VARIABLES.md updated. The `_format_event`
timestamp line keeps HH:MM:SS, but 24h windows warrant the date too —
switched to `MM-DD HH:MM:SS`.

24h matches the natural staging soak window: deploy to
hetzner-staging on day N, run `make soak` on day N+1 → promote.

## 3. Feature-completeness lint `verify-feature-complete`

New `poc/scripts/verify_feature_complete.py` + Makefile target.
Runs in `make check` and the pre-push hook.

Rules enforced:
- Any `docs/stories/BC-NNN-*.md` with `status: shipped` must have
  its paired `docs/plan/BC-NNN-*-plan.md` shipping-evidence table
  contain ZERO `❌` or `⚠️` rows. If a story is marked shipped but
  the plan still has holes, the lint names the holes and blocks
  the push.
- Any `docs/stories/BC-NNN-*.md` with `status: in-progress` is
  allowed to have `❌` rows (work in progress).
- Any `docs/plan/BC-NNN-*-plan.md` without a paired story file is
  flagged (orphaned plan).

Bypass: `BEACONRY_SKIP_FEATURE_COMPLETE=1` for emergencies.

Runs locally so the operator catches it before pushing — as
requested, "This could happen locally as well".

## 4. CI-failure learning log `docs/plan/CI-FAILURES.md`

New append-only log. One section per CI failure with:
- Date + run URL
- Trigger (which commit/PR)
- Root cause (one paragraph)
- Fix (link to the BC that fixed it)
- Regression test added (file:line)
- "What we learned" one-liner

Backfilled with two entries:
- 2026-11-02 run 41503095888 → BC-071 (BEACONRY_SKIP_VERSION_BUMP env leak)
- Dependabot PR #1 (not a CI failure but same category — "surprise unsafe content" fixed via BC-072)

Discipline: every subsequent "fix a CI failure" BC appends an
entry. Not automatically enforced (hard to lint "did you add a
log entry?") — just documented in `memory/workflow.md` as part of
the fix-a-regression playbook.

## Version

`0.3.7 → 0.4.0` (minor — workflow + release-flow change, visible
to operator; reverses the no-auto-promote line from CAP-T147).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| release.yml exists + parses | unit | `test_BC_074_release_automation.py::test_release_yaml_parses` | ✓ |
| release.yml only triggers on workflow_dispatch | unit | `test_BC_074_release_automation.py::test_release_yaml_dispatch_only` | ✓ |
| release.yml does ff-main + tag + push steps | unit | `test_BC_074_release_automation.py::test_release_yaml_has_ff_main_and_tag_steps` | ✓ |
| Soak default is 24h | unit | `test_BC_074_release_automation.py::test_soak_default_window_is_24h` | ✓ |
| Soak timestamp line includes month-day | unit | `test_BC_074_release_automation.py::test_soak_timestamp_shows_month_day` | ✓ |
| Soak window override still shrinks window | unit | `test_BC_074_release_automation.py::test_soak_window_override_still_works` | ✓ |
| release.yml requires contents:write permission | unit | `test_BC_074_release_automation.py::test_release_yaml_requires_contents_write` | ✓ |
| verify-feature-complete ignores `_template.md` | unit | `test_BC_074_release_automation.py::test_verify_feature_complete_ignores_template_files` | ✓ |
| verify-feature-complete blocks shipped-with-✓ | unit | `test_BC_074_release_automation.py::test_verify_feature_complete_blocks_shipped_with_open_rows` | ✓ |
| verify-feature-complete allows in-progress-with-✓ | unit | `test_BC_074_release_automation.py::test_verify_feature_complete_allows_in_progress_with_open_rows` | ✓ |
| verify-feature-complete flags orphan plans | unit | `test_BC_074_release_automation.py::test_verify_feature_complete_flags_orphan_plans` | ✓ |
| verify-feature-complete passes on live repo | unit | `test_BC_074_release_automation.py::test_verify_feature_complete_passes_live` | ✓ |
| verify-feature-complete bypass env | unit | `test_BC_074_release_automation.py::test_verify_feature_complete_bypass_env` | ✓ |
| CI-FAILURES.md exists + has backfilled entries | unit | `test_BC_074_release_automation.py::test_ci_failures_log_present` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

**CAP-T147 scope changes** — "NO auto-merge dev→main" replaced with
"gated auto-promote via workflow_dispatch". Visible to anyone who
reads the CAP; operator was the one asking. Minor bump reflects.
No HTTP/data contract changes.

## Rollout

- `make bump-minor` → 0.4.0 (feature-level change; reverses prior
  design decision).
- Merge to dev.
- First release: operator clicks "Run workflow" on
  `.github/workflows/release.yml`. CI already green from last push.
  Workflow ff-main + tags + pushes. Done.
