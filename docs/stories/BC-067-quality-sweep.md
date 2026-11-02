---
id: BC-067
title: Quality sweep — 5 severity-1 bugs from the autonomous audit
status: shipped
created: 2026-11-02
branch: fix/BC-067-quality-sweep
plan: docs/plan/BC-067-quality-sweep-plan.md
---

# BC-067: Quality sweep

Operator requested: *"do some investigation to improve the overall
quality of beaconry. find at least 3 high severe bugs in the code
and fix them. prioritize clearly. the rest put into the ideas. act
on your own as long you dont change the ux for the user."*

Three parallel investigators across concurrency/data-integrity, CVE
pipeline correctness, and security found 12 issues. Five
severity-1 bugs shipped; the rest moved to `docs/plan/IDEAS.md`.

## Fixed

1. **Shell injection in DockerAdapter** (CRITICAL — security).
   `source_ref` from SBOM into `create_subprocess_shell` f-string.
   Fix: `create_subprocess_exec` + argv list.
2. **`/test/*` routes unauthenticated** (CRITICAL — security).
   `BEACONRY_TEST_MODE=1` left on in production exposed DB wipe,
   finding injection, status forgery.
   Fix: `Depends(require_cli_token)` on all four routes.
3. **`vuln["id"]` KeyError silently drops findings** (SEVERE —
   correctness). One malformed grype row could silently lose a CVE.
   Fix: `.get("id")` + `invalid_grype_match` audit event.
4. **Reconcile mass-closes on partial inventory** (SEVERE —
   correctness). Scout glitch → mass auto-close → next scan floods
   operator with regressions. Fix: in-memory per-host SBOM-count
   baseline + 50% threshold.
5. **Double-reminder on same tick** (MODERATE — UX noise). Fix:
   swap sweep order so manual runs before heartbeat.

## Deferred to IDEAS

7 lower-severity items — hash truncation at scale, audit-log fire-
and-forget semantics, reconcile TOCTOU, fix-version-empty-string,
reconcile UPDATE-phase conn leak, SBOM payload DoS, API header on
CORS preflight responses. All in `docs/plan/IDEAS.md` with triage
notes.

## Behaviour change

None visible to operators. Internal correctness + security fixes
only. Patch bump 0.3.0 → 0.3.1.
