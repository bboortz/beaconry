---
name: code-quality
description: Commit format, branch naming, plan template convention, pre-PR checklist for beaconry
metadata:
  type: reference
---

*Adapted from kernel-test/memory/code-quality.md and homelab/memory/code-quality.md.*

## Commit message format

```
<type>(<scope>): <description>
```

Types: `feat` `fix` `docs` `refactor` `chore` `ci` `test` `style`

Scopes: `scout` `hub` `executor` `notifications` `docker` `docs`

Examples:
- `feat(scout): add pacman DB parser for Arch packages`
- `fix(hub): escape Markdown in CVE description before sending`
- `docs(poc): add demo script and setup instructions`
- `refactor(executor): extract base class from DockerAdapter`

## Branch naming

```
feat/BC-NNN-<slug>     new feature (story-driven)
fix/<slug>             bug fix (clear repro, no story required)
docs/<slug>            docs only
refactor/<slug>        no behaviour change
chore/<slug>           tooling, dependencies, config
```

Every `feat/*` and `fix/*` branch needs `docs/plan/<slug>-plan.md`. See `workflow.md` for the full DD-TGD process.

## When creating a branch

1. Confirm the story (`docs/stories/BC-NNN-<slug>.md`) is in status `ready` (feat only)
2. Branch from `dev`, not from `main`
3. Create `docs/plan/<slug>-plan.md` from `docs/plan/_template.md`
4. Flip story status to `in-progress`
5. PR target is **`dev`** — never `main`

## Before creating a PR

Delegate to the gates in `workflow.md`: `make check` must pass locally, then open the PR. Three pre-PR sanity questions still apply:

- Is there an edge case or failure mode not covered?
- Is there a simpler way to achieve the same result?
- Does any part contradict or duplicate existing behaviour?

PR title: under 70 chars, no trailing period. PR body: Claude writes from branch diff — links story + plan doc, summary bullets + test plan checklist.

## Python style (ruff)

`ruff check` + `ruff format` on all `.py` files before push.
Fix with: `ruff format $(git ls-files "*.py")` and `ruff check --fix .`

## Memory file size limit

`memory/*.md` files ≤ 150 lines. Keep them focused and scannable.
`CLAUDE.md` ≤ 150 lines — @-includes are loaded separately.
