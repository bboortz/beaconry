---
id: BC-069
title: Release toolchain — CI pipeline + make soak + auto-CHANGELOG
status: shipped
created: 2026-11-02
branch: feat/BC-069-release-toolchain
plan: docs/plan/BC-069-release-toolchain-plan.md
---

# BC-069: Release toolchain

Three interlocking deliverables so finalisation moves from
discipline to signal. Operator rules honoured: `main` only merges
manually, local checks stay primary, CI is a safety net.

## Shipped

### 1. GitHub Actions CI (`.github/workflows/ci.yml`)

Stratified gates by branch:

- `feat/**` / `fix/**` / `docs/**` → ruff + ruff-format + pyright (~20s)
- `dev` push + PRs to `dev` → `make check` (full fast tier, ~2min)
- `main` push → `verify-changelog` + `verify-version-sync` + tag advisory

Adopted from homelab: concurrency cancellation, docs-only
short-circuit, pip caching, optional Telegram failure ping (reuses
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` secrets if set).

**Explicitly NOT adopted**: auto-merge dev → main. Operator keeps
that gate manual.

### 2. `make soak`

`poc/scripts/soak.py` scans `$DATA_DIR/audit.log` for events within
`SOAK_WINDOW_S` (default 1h) whose event name matches a curated
error set. Exit 0 clean, 1 if errors in window (listed by ts +
host + CVE), 2 if audit log unreadable. Operator runs after each
`make live-rebuild` to confirm the deploy didn't introduce new
error-shaped events.

### 3. CHANGELOG.md auto-generation

`poc/scripts/changelog.py` reads commits since the last `## [v*]`
header, groups by conventional-commit type (feat → Added, fix →
Fixed, chore/refactor/perf → Changed, docs → Documentation, `!:`
and `BREAKING CHANGE:` → ⚠ Breaking block). Keep A Changelog
format. `bump_version.py` invokes it as a post-write hook — bump
VERSION, CHANGELOG regenerates automatically.

`verify_changelog.py` + `make verify-changelog` + pre-push hook
refuses pushes if `VERSION` isn't present as a `## [v<VERSION>]`
header. Emergency bypass: `BEACONRY_SKIP_CHANGELOG=1`.

For incremental deltas (section lists only the new commits), tag
`v<VERSION>` after each release:

```
git tag v0.3.3
git push origin v0.3.3
```

Without a tag, the generator falls back to full history — safe
but duplicates older bullets. Fallback documented in
`test_changelog_without_tag_falls_back_to_full_history`.

## Version

0.3.2 → 0.3.3 (patch — tooling only, no runtime behaviour change).
