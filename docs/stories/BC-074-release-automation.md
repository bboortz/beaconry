---
id: BC-074
title: Release automation + 24h soak + feature-completeness lint + CI-failure log
status: shipped
created: 2026-11-02
branch: feat/BC-074-release-automation
plan: docs/plan/BC-074-release-automation-plan.md
---

# BC-074: Release automation + related guards

Operator asked for a streamlined, fully-automated release cycle
("release fast and often") with four interlocking pieces:

1. **CI auto-promote dev → main** — gated by `workflow_dispatch` so
   the operator keeps a review step but automates everything
   downstream of the click.
2. **Soak default raised to 24h** — matches the natural staging
   soak length (deploy day N → verify day N+1 → promote).
3. **Feature-completeness lint** — ensure "whole features have been
   implemented" by refusing to let a `status: shipped` story
   coexist with ❌ rows in its paired plan.
4. **CI-failures learning log** — every CI failure gets an entry
   with trigger / root cause / fix / regression test / lesson, so
   the issue "never happens twice".

See the plan doc for the full design + shipping evidence table.

## Reversal note

This reverses CAP-T147's "NO auto-merge dev → main" line. The
earlier choice was manual-only; the operator now explicitly asks
for automation. Preserved as a reviewable gate via
`workflow_dispatch` instead of auto-on-push.

Version: 0.3.7 → 0.4.0 (minor — workflow change visible to operator).
