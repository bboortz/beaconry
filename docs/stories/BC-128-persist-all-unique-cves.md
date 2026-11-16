---
id: BC-128
title: Persist every identified finding; cap only notifications
status: shipped
created: 2026-11-16
branch: feat/BC-128-persist-all-unique-cves
plan: docs/plan/BC-128-persist-all-unique-cves-plan.md
---

# BC-128: `beaconry findings` must show every identified CVE, not just top-N

## Narrative

Hard assessment on server2 (post-BC-127 deploy) found a hub-side
bug: `BEACONRY_MAX_FINDINGS_PER_RUN=100` was designed as a
notification-rate cap but was coded as a persistence cap too.

Every server2 scan:

```
n_candidates:   2029
n_unique:        318  (2029 - 1711 dedup)
n_top_100:       100  ← save_finding + notify()
n_suppressed:    218  ← digest only, NEVER PERSISTED
```

Result: **218 unique CVEs per scan are silently lost from the DB**,
every scan, indefinitely. `beaconry findings --host server2 --severity
Critical` returns only the top-ranked Criticals; lower-ranked ones
in the suppressed tail never get a DB row.

Post-BC-128: every identified unique CVE is persisted. The
notification cap still applies, but only to the `notify()` /
Telegram step. Operator sees the full surface via
`beaconry findings`; operator inbox still gets at most
`BEACONRY_MAX_FINDINGS_PER_RUN` Telegram messages per scan + one
digest for the rest.

## Trigger

- Every scan on any host with more unique CVEs than
  `BEACONRY_MAX_FINDINGS_PER_RUN` (today: all three hosts).
- Operator running `beaconry findings --severity Critical --host X`
  expecting a complete critical-list.

## Expected outcome

- `save_finding(f)` runs on **every unique CVE** in the scan
  (`to_notify + suppressed`), not only the top-N.
- `notify(f)` + `await asyncio.sleep(0.3)` still runs only on the
  top-N (operator inbox unchanged).
- `send_digest(suppressed)` still fires for the suppressed tail
  (operator digest unchanged).
- `scan_integrity` audit event gets a new `n_notified` field (the
  top-N count) so the balance is still auditable:
  `n_candidates == n_persisted + n_dedup + n_dropped_in_save + n_filtered_other`
  with `n_persisted` now counting both top-N + tail.
- `n_capped` is renamed to `n_suppressed_from_notify` to reflect
  its new semantics (dropped-from-notification, NOT dropped-from-DB).
- After deploy + one scan: `beaconry findings --host server2` grows
  from 100 to ~318 rows.
- `beaconry findings --severity Critical --host server2` reveals
  every Critical, not just top-N ranked.

## Out of scope

- Changing `BEACONRY_MAX_FINDINGS_PER_RUN` default (operator-tuned
  per deploy).
- Changing the Telegram digest body format.
- Backfilling the 11h-stale findings on server2 — those simply
  get re-persisted on the next scan after deploy.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Hub persists every identified unique CVE; notification cap is separate

  Scenario: Suppressed CVEs are persisted (the BC-128 core change)
    Given 318 unique CVEs in a scan on server2
    And BEACONRY_MAX_FINDINGS_PER_RUN=100
    When _ingest_candidates runs
    Then save_finding is called 318 times (not 100)
    And 218 of those are new (previously-suppressed CVEs)
    And notify() is called only on the top-100

  Scenario: scan_integrity audit balance still holds
    Given 2029 candidates, 318 unique, 100 notified, 218 suppressed-from-notify
    When scan_integrity fires
    Then n_persisted = 318 (unique count, assuming all new)
    And n_notified = 100
    And n_suppressed_from_notify = 218
    And n_candidates == n_persisted + n_dedup + n_filtered_other (invariant)

  Scenario: Telegram operator inbox unchanged
    Given 318 unique CVEs
    When _ingest_candidates runs
    Then at most 100 individual Telegram messages fire
    And 1 digest message fires for the suppressed-from-notify tail

  Scenario: beaconry findings reveals the full surface
    Given post-BC-128 hub ran one scan on server2
    When operator runs `beaconry findings --host server2`
    Then approximately 318 rows are returned (not 100)
    And `beaconry findings --host server2 --severity Critical` shows
      every Critical, including those in the previously-suppressed tail

  Scenario: notify failure on a suppressed finding (impossible by design)
    Given a suppressed-from-notify CVE is persisted
    When _ingest_candidates runs
    Then notify() is NOT called for it (no notify_failed audit)
    And it still gets a DB row + appears in the digest body
```

## Capabilities delivered

- **CAP-T231: Persist every identified unique CVE; separate notification cap**
  (type: technical, status: active)

  *`_ingest_candidates` runs `save_finding` on both `to_notify`
  (top-N by rank) and `suppressed` (tail). `notify()` + 0.3s
  rate-limit still only fires on `to_notify`. Digest still fires on
  `suppressed` for operator inbox parity. `scan_integrity` audit
  event gains `n_notified` (top-N count) and renames `n_capped` →
  `n_suppressed_from_notify`. `n_persisted` now counts BOTH
  branches; balance invariant updates to:
  `n_candidates == n_persisted + n_dedup + n_dropped_in_save + n_filtered_other`.
  Previous invariant (which counted n_capped as a drop bucket)
  held when persistence == top-N; after BC-128 the suppressed
  items are persisted, so n_capped is no longer a drop.*

Extends CAP-T224 (POLICIES line — MAX_FINDINGS_PER_RUN's semantics
clarified), CAP-T207 (findings CLI surface), CAP-T223 (integrity
accounting).

## Open questions

Resolved during drafting (2026-11-16):

1. Rename `BEACONRY_MAX_FINDINGS_PER_RUN`?
   **No.** Keep for operator compatibility. The env now controls
   only the notification cap, which is what most operators think
   it already does.
2. Should `save_finding` on suppressed items emit a different
   audit event than top-N saves?
   **No.** Same `finding_ingested` audit event (if it fires per
   save — need to check). Operator forensics stay simple.
3. Rate-limit `save_finding` for the tail?
   **No.** DB inserts are cheap (<1ms); no need for the 0.3s
   sleep that's between `notify()` calls.
4. Should the digest body still show suppressed items now that
   they're persisted?
   **Yes.** Operator still wants the Telegram glance at what was
   below the top-N. Just because it's in the DB doesn't mean
   operator checked the DB.

## Links

- Related: BC-005 (dedup_and_cap origin), BC-086 (reconcile_scan
  uses scanned_ids superset, already correct), BC-122 (audit
  balance), BC-123 (POLICIES line), BC-127 (--severity filter
  that revealed the gap).
