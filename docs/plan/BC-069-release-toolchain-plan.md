# BC-069 — Release toolchain: CI + make soak + CHANGELOG

*Branch: `feat/BC-069-release-toolchain`*
*Start: 2026-11-02*

Three interlocking deliverables so finalisation moves from
discipline-based to signal-based. All three honour operator rules:
`main` only merges manually (CI never force-promotes), local checks
stay primary, pipeline is a safety net not the authority.

## 1. GitHub Actions CI (`.github/workflows/ci.yml`)

### Stratified gates (speed first)

| Trigger | Runs | Target budget |
|---|---|---|
| Push to `feat/**`, `fix/**`, `docs/**`, `refactor/**`, `chore/**` | ruff + pyright only (lint fast tier) | ~20s |
| Push to `dev` (direct or via local ff) | `make check-full` (lint + type + full pytest + coverage ≥85% + all drift lints + smoke + scenarios + CLI smokes) | ~2min |
| Push to `main` | `make verify-changelog` + `make verify-version-sync` + tag presence check | ~15s |
| Pull request to `dev` | Same as push-to-dev (same gate, both event types) | ~2min |

### Patterns adopted from homelab

- **Concurrency cancellation**: in-flight run gets killed on new push to the same ref. Saves CI minutes.
- **docs-only short-circuit**: if the diff is only `*.md` or `docs/**`, lint runs but heavy tests skip.
- **pip cache keyed on `requirements-dev.txt`**.
- **Telegram failure notification**: reuses `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` org secrets (same bot the operator already configured).

### NOT adopted

- Ansible / Tailscale / Terraform / SSH deploy — not relevant.
- Auto-merge dev → main — operator explicitly reserves this gate.
- "Already-validated SHA" skip — adds complexity for a one-reviewer setup.

### Secrets required

```
TELEGRAM_BOT_TOKEN   # optional — failure ping; omit → silent fail
TELEGRAM_CHAT_ID     # optional
```

No vault, no SSH keys, no cloud creds. Public repo, public tests.

## 2. `make soak` + `poc/scripts/soak.py`

Operator runs it after a deploy to answer *"is the stack healthy
over the last hour, or did something new break?"*

### What it checks

Reads the audit log (`$DATA_DIR/audit.log`, default
`.beaconry-cache/audit.log` for local dev, `/data/audit.log` in
container), filters events within the last `SOAK_WINDOW_S` seconds
(default 3600 = 1h), and flags any event matching the "something
went wrong" set:

```python
_ERROR_EVENTS = {
    "notify_sent_failed",            # Telegram or adapter blew up
    "invalid_grype_match",           # BC-067: malformed CVE data
    "reconcile_skipped_partial_inventory",  # BC-067: scout glitch
    "regressed",                     # BC-031: fix regression — SEVERE
    "set_status_failed",             # operator action failed
    "manual_fix_reminder_sent",      # BC-066: operator never acted
}
```

For each error event, prints the timestamp, event type, CVE id, and
host. Exit codes:

- `0` — clean (no error events in window)
- `1` — findings in window (operator should look)
- `2` — can't read audit log (missing file, permission denied)

### Operator-facing output

```
$ make soak
Scanning audit.log (last 1h, since 2026-11-02 17:12:34)
✓ 0 error events in last 1h — stack looks healthy
```

```
$ make soak
Scanning audit.log (last 1h, since 2026-11-02 17:12:34)
⚠ 3 error events in last 1h:
  17:34:12  notify_sent_failed      finding_id=d8e2f1a3  cve_id=CVE-2026-00099
  17:48:21  regressed               finding_id=f0b442fb  cve_id=CVE-2024-3094  host=server2
  17:51:03  invalid_grype_match     host=hetzner-staging  artifact=libjunk
```

### Env knobs

- `SOAK_WINDOW_S` (default 3600) — override the window length.
- `DATA_DIR` (default resolves to `.beaconry-cache` locally) — audit log location.

## 3. CHANGELOG.md + `make changelog`

### Shape

Keep A Changelog format, so it's human + tool readable. Headers:

```markdown
# Changelog

All notable changes to beaconry are recorded here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and this
project follows [Semantic Versioning](https://semver.org/).

## [v0.3.3] — 2026-11-02

### Added
- CI pipeline on GitHub Actions (BC-069)
…

### Fixed
- …

### Changed
- …

## [v0.3.2] — 2026-11-02
…
```

### Auto-generation

`poc/scripts/changelog.py` reads commits since the last
`## [v*]` header found in `CHANGELOG.md`, groups by conventional-
commit type, and prepends a new section for the current `VERSION`:

- `feat(...)` → **Added**
- `fix(...)` → **Fixed**
- `refactor(...)` / `chore(...)` / `perf(...)` / `style(...)` → **Changed**
- `docs(...)` → **Documentation** (only if ≥1 commit; otherwise omitted)
- `BREAKING CHANGE:` footer or `!:` → extra `### ⚠ Breaking` block

Commits that don't match conventional-commit form are listed under
**Other** so nothing silently disappears.

First run: no existing header; writes a full history dump since the
LICENSE commit.

### Integration with `make bump-*`

`bump_version.py` learns one new post-write hook: after rewriting
VERSION, invoke the changelog generator. Operator never has to
remember a second step.

### Enforcement

`make verify-changelog`: fails if `VERSION` doesn't appear as a
`## [v<VERSION>]` header in `CHANGELOG.md`. Wired into `make check`
AND run as a pre-push hook (so a push without a matching changelog
entry is blocked).

Opt-out: `BEACONRY_SKIP_CHANGELOG=1` — same emergency bypass
pattern as `BEACONRY_SKIP_VERSION_BUMP`.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Soak clean → exit 0 | unit | `test_BC_069_release_toolchain.py::test_soak_clean_window_returns_zero` | ✓ |
| Soak with error events → exit 1 + prints events | unit | `test_BC_069_release_toolchain.py::test_soak_errors_in_window_returns_one_and_prints` | ✓ |
| Soak missing audit log → exit 2 | unit | `test_BC_069_release_toolchain.py::test_soak_missing_audit_log_returns_two` | ✓ |
| Soak respects SOAK_WINDOW_S | unit | `test_BC_069_release_toolchain.py::test_soak_window_env_controls_cutoff` | ✓ |
| Soak drops malformed JSON lines | unit | `test_BC_069_release_toolchain.py::test_soak_malformed_lines_dropped` | ✓ |
| changelog groups commits by type | unit | `test_BC_069_release_toolchain.py::test_changelog_groups_commits_by_type` | ✓ |
| changelog breaking-change block | unit | `test_BC_069_release_toolchain.py::test_changelog_surfaces_breaking_changes` | ✓ |
| changelog first-run writes full history | unit | `test_BC_069_release_toolchain.py::test_changelog_first_run_includes_all_history` | ✓ |
| changelog write() persists file | unit | `test_BC_069_release_toolchain.py::test_changelog_write_persists_file` | ✓ |
| changelog second-bump prepends new section (tag-bounded) | unit | `test_BC_069_release_toolchain.py::test_changelog_second_bump_prepends_new_section` | ✓ |
| changelog without tag falls back to full-history dupe | unit | `test_BC_069_release_toolchain.py::test_changelog_without_tag_falls_back_to_full_history` | ✓ |
| verify-changelog fails when VERSION missing from log | unit | `test_BC_069_release_toolchain.py::test_verify_changelog_fails_on_missing_entry` | ✓ |
| verify-changelog fails when CHANGELOG.md absent | unit | `test_BC_069_release_toolchain.py::test_verify_changelog_fails_on_missing_file` | ✓ |
| verify-changelog passes when VERSION matches | unit | `test_BC_069_release_toolchain.py::test_verify_changelog_passes_when_matched` | ✓ |
| verify-changelog bypass env short-circuits | unit | `test_BC_069_release_toolchain.py::test_verify_changelog_bypass_env` | ✓ |
| CI yaml is syntactically valid | integration | `test_BC_069_release_toolchain.py::test_ci_yaml_parses` | ✓ |
| CI yaml uses concurrency cancellation | integration | `test_BC_069_release_toolchain.py::test_ci_yaml_uses_concurrency_cancellation` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

None. Version bump 0.3.2 → 0.3.3 (patch — tooling only).

## Rollout

- `make bump-patch` → 0.3.3 (now also auto-regenerates CHANGELOG.md thanks to new hook).
- Merge to dev. First CI run smokes the pipeline against GitHub.
- Tag `v0.3.3` on main after soak confidence (operator action, as
  per the branch model agreement).
