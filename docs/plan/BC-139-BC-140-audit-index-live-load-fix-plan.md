# BC-139 + BC-140 — Audit-index live-load fix (combined branch plan)

This branch ships two stories together as a single incident hot-fix:

- **BC-139** write-amplitude fix:
  `docs/plan/BC-139-audit-index-write-amplitude-plan.md`
- **BC-140** async decoupling:
  `docs/plan/BC-140-audit-index-async-decoupling-plan.md`

Each sub-plan has its own Shipping-evidence table. The branch's merge
plan is the union: BC-139 ships first (in code), BC-140 layers on top.
One PR, one release tag (`v0.22.17`).

## Shipping evidence (branch-level pointer)

| Gate | Evidence |
|---|---|
| BC-139 Gherkin covered | see `BC-139-audit-index-write-amplitude-plan.md` |
| BC-140 Gherkin covered | see `BC-140-audit-index-async-decoupling-plan.md` |
| Unit tests | `poc/tests/unit/test_BC_139_BC_140_audit_index_live_load.py` (13 tests across both CAPs) |
| make check | passes locally on this branch |
| make check-full | passes (BEACONRY_SKIP_DOGFOOD=1 for the unrelated python 3.14.10 base image CVE) |
| Live post-deploy | verify `/healthz` surfaces `audit_index_queue_depth` + `audit_index_dropped_events`; verify audit queries stay <100 ms during an active bb-82jq scan |
