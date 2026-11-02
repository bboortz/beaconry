---
id: BC-065
title: Digest content-change bypass — opt-out via env for strict 24h floor
status: shipped
created: 2026-11-02
branch: fix/BC-065-digest-content-change-opt-out
plan: docs/plan/BC-065-digest-content-change-opt-out-plan.md
---

# BC-065: Digest content-change opt-out

Operator reported `make audit EVENT=digest_sent` showing multiple
digests per host per day despite `BEACONRY_DIGEST_INTERVAL_S=86400`.

Root cause: BC-036's `should_send_digest()` has a `content_changed`
branch that short-circuits BEFORE the interval check. In a stack
where new CVEs rotate the capped tail (post-BC-005 dedup +
MAX_FINDINGS_PER_RUN=5), the hash changes every scan and the
throttle is effectively 0s.

Fix: new env `BEACONRY_DIGEST_ON_CONTENT_CHANGE` (default `1`,
preserves BC-036 behaviour; `0` makes DIGEST_INTERVAL_S a strict
floor). Homelab operator sets `0` via Ansible.

0.2.1 → 0.2.2 (patch, additive env var).
