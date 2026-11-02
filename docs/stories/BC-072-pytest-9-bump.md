---
id: BC-072
title: pytest 8 → 9 (CVE-2025-71176) + cascading pytest-asyncio bump
status: shipped
created: 2026-11-02
branch: fix/BC-072-pytest-9-bump
plan: docs/plan/BC-072-pytest-9-bump-plan.md
---

# BC-072: pytest 9 bump

Dependabot PR #1 proposed `pytest 8.3.3 → 9.0.3` but Dependabot
doesn't see transitive constraints — `pytest-asyncio 0.24.0` pins
`pytest<9`, so merging PR #1 would brick `pip install`.

Also: PR targeted `main`, our workflow has main as release-only.

## Fixed

Bumped both together on dev:

- `pytest 8.3.3 → 9.0.3` (fixes CVE-2025-71176 insecure temp dir)
- `pytest-asyncio 0.24.0 → 1.4.1.post1` (unblocks pytest<9 pin)

Verified `make check-full` end-to-end before shipping.

PR #1 should be closed after this lands — Dependabot will
re-evaluate against dev's new pins.

0.3.5 → 0.3.6 (patch, dev-dep only).
