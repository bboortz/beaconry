# BC-073 — python-dotenv + requests dep bumps (closes PRs #2, #3)

*Branch: `fix/BC-073-dep-bumps`*
*Start: 2026-11-02*

## Context

Dependabot opened:

- **PR #2**: `python-dotenv 1.0.1 → 1.2.2` (dev-only)
- **PR #3**: `requests 2.32.3 → 2.33.0` (runtime; `poc/scout/requirements.txt`)

Both content-safe (no transitive constraint traps unlike BC-072's
pytest case). Both target `main` instead of `dev` — same workflow
drift pattern we've been correcting. Bundle on dev, close PRs
after merge.

## Changes

```
requirements-dev.txt            python-dotenv 1.0.1 → 1.2.2
poc/scout/requirements.txt      requests      2.32.3 → 2.33.0
```

`pyproject.toml` has `requests>=2.31` so 2.33.0 is in range already.
No pins elsewhere in the repo.

## Verified

- `pip install -r requirements-dev.txt -r poc/scout/requirements.txt`
  resolves cleanly (no conflicts).
- `make check-full` green: 884 tests + 10/10 scenarios + 11/11 CLI
  smokes.

## Version

0.3.6 → 0.3.7 (patch — dependency bumps only, no API change).

## Follow-up

Still deferring `.github/dependabot.yml` with `target-branch: dev`
unless the operator asks for it. Would eliminate the recurring
target-branch-is-main issue; cheap but a separate decision.
