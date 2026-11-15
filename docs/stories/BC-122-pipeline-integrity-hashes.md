---
id: BC-122
title: Pipeline integrity — hashes + runtime verification that every finding is transferred + processed losslessly
status: shipped
created: 2026-11-15
branch: feat/BC-122-pipeline-integrity-hashes
plan: docs/plan/BC-122-pipeline-integrity-hashes-plan.md
---

# BC-122: End-to-end lossless evidence for the scout → hub pipeline

## Narrative

Operator asks: "How do I know every finding my scouts saw actually
made it to the hub and got processed?" Today the answer is "trust
the logs" — there's no machine-checkable evidence. A scout cycle that
silently dropped half its SBOMs (BC-107 retry queue broken, network
flap mid-POST, hub filter edge case) would look identical to a
healthy cycle from the operator's perspective.

Post-BC-122: four hashes stitch every stage of the pipeline together.
Scout computes `iteration_hash` (identifies "this scan cycle") and
`transfer_hash` (identifies "this POST body"), embeds both in the
/scan body, and logs them. Hub recomputes `transfer_hash` on receipt
and verifies the match (transfer integrity). After ingest, hub
computes `processing_hash` over the sorted persisted finding IDs and
emits a `scan_integrity` audit event with every delta accounted for
(n_sboms_received, n_candidates, n_persisted, n_filtered_min_cvss,
n_dedup). A new `beaconry verify-integrity` CLI walks the audit log
and reports any iteration where transfer_hash didn't verify, or
processing drops aren't fully explained by known filters.

## Trigger

- Every scout scan cycle (automatic).
- Operator running `beaconry verify-integrity [--since 24h]` to get
  a fleet-wide integrity report.

## Expected outcome

- Every /scan POST carries `iteration_hash` + `transfer_hash` + `n_sboms`.
- Hub audit log has one `scan_integrity` event per received scan.
- `beaconry verify-integrity` lists every iteration with:
  `iteration | host | transferred | match | n_persisted | filter_breakdown`.
- On transfer-hash mismatch, hub logs WARN + records the mismatch in
  the audit event. (Telegram alert gated by
  `BEACONRY_PIPELINE_INTEGRITY_ALERT=1`, default off until operator
  tunes signal.)
- Legacy pre-BC-122 scouts (no hashes in body) degrade cleanly: hub
  emits `scan_integrity` with `transfer_hash_claimed=null,
  transfer_match=null`, operator sees the gap in the verifier output.

## Out of scope

- Content integrity of the SBOMs themselves (that's a syft concern).
- Encryption / signing. Transfer hash prevents *accidental* corruption,
  not malicious tampering. A compromised scout could forge its own
  hash; this story is about non-malicious loss detection.
- Blocking scans on mismatch. Report-only.
- Retention / archiving of `scan_integrity` audit entries (existing
  audit log retention applies).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Every scan iteration is traceable end to end via hashes

  Scenario: Happy path — hashes match end to end
    Given a scout with 5 containers
    When scout runs a scan cycle
    Then the /scan body contains iteration_hash + transfer_hash + n_sboms
    And scout logs "BC-122: iteration=<h> sboms=5 transfer=<h>"
    And hub recomputes transfer_hash and verifies it matches
    And hub emits a scan_integrity audit event with iteration_hash + transfer_match=true + processing_hash + n_persisted

  Scenario: Transfer corruption — hash mismatch logged
    Given a /scan POST whose body was mutated mid-flight
    When hub recomputes transfer_hash
    Then transfer_match is false in the audit event
    And hub logs a WARN with both hashes
    And processing still runs (fail-open, not fail-closed)

  Scenario: Legacy pre-BC-122 scout — graceful degrade
    Given a scout that doesn't send iteration_hash / transfer_hash
    When hub receives the POST
    Then scan_integrity audit event records transfer_hash_claimed=null + transfer_match=null
    And processing proceeds normally

  Scenario: verify-integrity CLI reports clean fleet
    Given 24h of audit logs with transfer_match=true on every scan
    When operator runs `beaconry verify-integrity`
    Then exit code is 0
    And output shows "✓ N iterations verified clean"

  Scenario: verify-integrity CLI flags a mismatch
    Given one scan_integrity event with transfer_match=false
    When operator runs `beaconry verify-integrity`
    Then exit code is non-zero
    And the mismatched iteration is listed with its hashes

  Scenario: `beaconry status` surfaces the mismatch count
    Given hub has recorded 3 pipeline_integrity_mismatches since startup
    When operator runs `beaconry status`
    Then the beaconry-hub DETAILS cell contains `⚠ integrity: 3 mismatches`
    And a healthy hub (counter=0) shows no integrity annotation

  Scenario: Processing drops are fully explained
    Given hub receives 10 candidates, filters 3 by MIN_CVSS + dedups 2
    When `scan_integrity` audit fires
    Then n_candidates=10, n_persisted=5, n_filtered_min_cvss=3, n_dedup=2
    And verifier confirms 10 = 5 + 3 + 2 (every drop accounted for)
```

## Capabilities delivered

- **CAP-T223: Pipeline integrity hashes + runtime verifier**
  (type: technical, status: active)
  *Scout computes stable `iteration_hash` (sha256-trunc16 over
  host+iteration_ts+sorted(image_refs)) and `transfer_hash`
  (sha256-trunc16 over the canonical scan body) per scan cycle,
  embeds both in the /scan POST body alongside `n_sboms`, and logs
  them. Hub's `receive_scan` recomputes `transfer_hash` on receipt,
  emits a `scan_integrity` audit event with the claimed+computed
  hashes, match bool, SBOM count, candidate count, persisted count,
  and filter-reason deltas (`n_filtered_min_cvss`, `n_dedup`,
  `n_filtered_other`). `processing_hash` is computed over sorted
  persisted finding IDs so a verifier can re-check which findings
  this iteration produced. On mismatch hub logs WARN; Telegram
  alert is opt-in via `BEACONRY_PIPELINE_INTEGRITY_ALERT=1`
  (default off). `poc/scripts/verify_integrity.py` + `beaconry
  verify-integrity` walk the audit log, surface mismatches, and
  verify every processing drop is accounted for (candidates =
  persisted + sum(filter_reasons)). Legacy pre-BC-122 scouts
  degrade cleanly — hub audit records `transfer_hash_claimed=null`
  + `transfer_match=null`; verifier flags coverage gaps without
  crashing.*

Supports CAP-T207 (findings visibility) and CAP-B023 (observability).
Extends CAP-T154 (audit log).

## Open questions

Resolved before implementation (2026-11-15):

1. Scout-side finding hash vs. hub-side finding hash?
   **Hub-side.** Findings only materialize after grype runs on the
   hub. Scout can't compute them. The pipeline is SBOMs-in, findings-out
   across the boundary. Reuse `finding.id` for per-finding identity.
2. Fail-open or fail-closed on mismatch?
   **Fail-open.** Processing continues. A transfer corruption should
   surface loudly but not block the scan path — operator wants
   evidence, not downtime.
3. Alert cadence?
   **Opt-in (default off).** First week of data will tell us baseline
   false-positive rate; then flip default to on if quiet.
4. Hash width?
   **16 hex chars.** Same pattern BC-005 chose for `finding.id`
   (sha256-trunc16 — birthday collision ≈ 2^32 iterations, way beyond
   any real fleet). Short enough for logs + Telegram.
5. Does `/healthz` surface mismatch count?
   **Yes.** `hub /healthz.checks.pipeline_integrity_mismatches: int`
   — monotonic counter since process start. `make status` operators
   see "pipeline_integrity 3 mismatches" if the counter climbs.

## Links

- Related: BC-107 (retry queue — cases BC-122 verifies didn't drop),
  BC-120 (fault-degradation surface), BC-029 (audit log origin),
  BC-105 (last_status_change_ts the verifier reads).
