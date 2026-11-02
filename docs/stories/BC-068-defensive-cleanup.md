---
id: BC-068
title: Defensive cleanup — reconcile_scan FD-leak guard + fix_version blank sanitize
status: shipped
created: 2026-11-02
branch: fix/BC-068-defensive-cleanup
plan: docs/plan/BC-068-defensive-cleanup-plan.md
---

# BC-068: Defensive cleanup

Two low-cost items from the BC-067 audit backlog that are both cheap
to fix and worth the defensive hardening. No UX change.

## Fixed

1. **reconcile_scan FD leak on UPDATE-phase OperationalError**.
   Wrapping the body in `try/finally con.close()` so a sqlite lock
   raised DURING the UPDATE loop (not just at the opening SELECT)
   still closes the connection. Previously would leak one FD per
   failure, which over thousands of scans exhausts the process limit.

2. **fix_version blank-string sanitize**. grype occasionally emits
   `fix.versions: [""]` or `[None]` (data artifact from legacy
   advisories). Previously the first blank entry propagated into
   the Finding as `fixed_version=""` and the operator saw
   "Fixed version: (blank)" in the notification. Now filter blanks
   so a legitimate "no fix" value renders as `"not available"`.

Both fixes land in `process_sbom_entry` + the parallel
`_build_findings_from_matches` in `demo.py`.

## Not fixed (still in IDEAS)

5 of 7 BC-067 leftovers remain deferred — hash truncation collision
risk, audit OSError semantics (by-design), reconcile TOCTOU under
concurrency, SBOM payload DoS (threat-model dependent), and
X-Beaconry-API fingerprinting. None are biting at POC scale.

## Version

0.3.1 → 0.3.2 (patch — defensive hardening, no behaviour change).
