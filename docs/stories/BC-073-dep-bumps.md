---
id: BC-073
title: python-dotenv + requests dep bumps (closes PRs #2, #3)
status: shipped
created: 2026-11-02
branch: fix/BC-073-dep-bumps
plan: docs/plan/BC-073-dep-bumps-plan.md
---

# BC-073: Dependency bumps

Dependabot PRs #2 (python-dotenv) and #3 (requests). Both
content-safe but targeted `main` instead of `dev`. Bundled here.

## Shipped

- `python-dotenv 1.0.1 → 1.2.2` (dev-only; `poc/demo.py::load_dotenv`)
- `requests 2.32.3 → 2.33.0` (scout runtime; `poc/scout/requirements.txt`)

`make check-full` verified clean. 0.3.6 → 0.3.7.
