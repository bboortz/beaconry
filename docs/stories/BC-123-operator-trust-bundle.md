---
id: BC-123
title: Operator trust bundle — audit every silent drop + surface every policy + CVE filter
status: shipped
created: 2026-11-16
branch: feat/BC-123-operator-trust-bundle
plan: docs/plan/BC-123-operator-trust-bundle-plan.md
---

# BC-123: Close the "operator can't see what's being dropped" class of bug

## Narrative

Yesterday's forensic walk through server2 caught a class of silent
drops the operator had no way to see. 48 CVSS≥9.0 CVEs per scan were
being thrown away because `BEACONRY_MAX_FINDINGS_PER_RUN=5`. The cap
was invisible in `beaconry status`, invisible in `beaconry findings`,
invisible in the audit log. BC-122 just shipped end-to-end integrity
hashes — but hashes don't help when the policy itself is hidden.

This story closes the broader class:

- Every finding the hub drops gets a dedicated audit event with a
  reason tag — not just a `continue` with a log line.
- Every policy knob the hub honors (MIN_CVSS, MAX_FINDINGS_PER_RUN,
  AUTO_FIX_THRESHOLD, SCAN_INTERVAL, EXECUTOR) is surfaced in
  `beaconry status` under a new POLICIES section — the operator sees
  their own configuration without reading env.
- `beaconry findings --cve <id>` + `make findings CVE=<id>` close
  the long-outstanding filter gap (the one I promised three times
  and worked around with grep each time).
- Telegram snooze buttons render their absolute expiry time so the
  operator knows "⏰ 1h" means "⏰ 1h → 00:47 UTC" — no more "did
  that tap work?" anxiety.
- BC-122's in-memory counter (`_pipeline_integrity_mismatches`) +
  per-host cache persist to SQLite so hub restart doesn't lose the
  signal.

## Trigger

- Every scan the hub processes (data-path audits + persistence).
- Every operator-initiated `beaconry status` / `beaconry findings`
  / Telegram tap.

## Expected outcome

- `beaconry audit --event=finding_filtered` returns one row per
  dropped candidate with its reason tag (`min_cvss`,
  `sbom_processing_failed`, `notify_failed`).
- `beaconry status` shows a POLICIES line:
  `MIN_CVSS=9.0 · MAX_FINDINGS_PER_RUN=100 · AUTO_FIX_THRESHOLD=3 · SCAN_INTERVAL=300s · EXECUTOR=manual`.
- `beaconry findings --cve GO-2026-6888` returns only that CVE's rows.
- Telegram snooze buttons: `⏰ 1h (→ 00:47)`.
- Hub restart: `/healthz.pipeline_integrity_mismatches` restores from
  disk (not 0).

## Out of scope

- Raising the default `MAX_FINDINGS_PER_RUN` cap — operator decision
  (homelab PR already shipped bumping it to 100).
- Making the digest Telegram body include the suppressed-finding
  list (it already does; this is about DB-side visibility).
- Rewriting the executor plugin interface.
- BC-117/BC-119 integration test backfill (BC-124).
- Scout heartbeat retry queue (BC-124).
- Hub port env var (BC-124).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Operator can see every drop + policy via CLI

  Scenario: MIN_CVSS drop is audited
    Given a CVE with CVSS 6.5 (below MIN_CVSS=9.0)
    When process_sbom_entry runs
    Then a `finding_filtered` audit event fires
    And the extra block carries `reason=min_cvss cvss=6.5 cve_id=<id> host=<h>`

  Scenario: SBOM processing failure is audited
    Given process_sbom_entry raises an exception on one of N SBOMs
    When receive_scan continues to the next SBOM
    Then a `sbom_processing_failed` audit event fires with the exception string
    And scan_integrity event's n_sbom_errors reflects the count

  Scenario: Notify failure routes to the retry queue
    Given save_finding succeeded but notify() raised
    When _ingest_candidates handles the exception
    Then pending_notifications has the finding_id queued
    And operator sees the retry happen on the next BC-104 sweep

  Scenario: beaconry status shows policy knobs
    When operator runs `beaconry status`
    Then a line "POLICIES: MIN_CVSS=... MAX_FINDINGS_PER_RUN=... AUTO_FIX_THRESHOLD=... SCAN_INTERVAL=... EXECUTOR=..." appears
    And every knob's current value is live (not hardcoded)

  Scenario: beaconry findings --cve filters by CVE id
    Given 100 findings in the DB
    When operator runs `beaconry findings --cve GO-2026-6888`
    Then only rows matching that CVE id are returned
    And the filter works over HTTP too

  Scenario: Telegram snooze button shows expiry
    Given a finding is notified at 23:47 UTC
    When operator sees the inline keyboard
    Then buttons render as `⏰ 1h (→ 00:47)`, `⏰ 4h (→ 03:47)`, etc.
    And the ack message after tap confirms the absolute expiry

  Scenario: Integrity counter persists across restart
    Given hub recorded 3 pipeline_integrity_mismatches
    When hub restarts
    Then /healthz.checks.pipeline_integrity_mismatches == 3 (not 0)
    And `beaconry status` DETAILS shows `⚠ integrity: 3 mismatches`
```

## Capabilities delivered

- **CAP-T224: Finding-filter audit + hub policy surface + CVE filter**
  (type: technical, status: active)

  *Hub emits per-drop audit events: `finding_filtered` (reason tag
  `min_cvss` / `other`), `sbom_processing_failed` (one per SBOM
  exception), and routes save-then-notify failures into BC-104's
  pending_notifications. `scan_integrity` (BC-122) gains
  `n_sbom_errors` + `n_filtered_min_cvss` fields so the verifier
  balance-sheet is complete. `beaconry status` renders a POLICIES
  line from `/healthz.checks` (new keys: `min_cvss`,
  `max_findings_per_run`, `scan_interval_s`). `beaconry findings
  --cve <id>` / `make findings CVE=<id>` adds the filter at both the
  local-DB and HTTP paths; endpoint takes a new `cve_id` query
  param. TelegramAdapter's snooze keyboard formatter adds a
  `(→ <HH:MM>)` suffix to every button label, computed from
  `datetime.utcnow() + timedelta(hours=N)`.*

Also:

- **CAP-T225: Durable pipeline-integrity counters**
  (type: technical, status: active)

  *New SQLite table `integrity_state` (schema v11 → v12) with columns
  `host TEXT PRIMARY KEY`, `iteration_hash TEXT`, `transfer_hash
  TEXT`, `last_updated_ts INTEGER`. `_last_scan_integrity_per_host`
  loads from this table on boot. New `integrity_counters` table with
  columns `key TEXT PRIMARY KEY, value INTEGER` stores
  `pipeline_integrity_mismatches` monotonically. Loaded at module
  import; incremented + flushed on every mismatch.*

Supports CAP-T207, CAP-T223, CAP-T154, CAP-B023.

## Open questions

Resolved before implementation (2026-11-16):

1. Audit event name — `finding_filtered` vs `finding_dropped`?
   **finding_filtered.** "Dropped" implies loss; "filtered" implies
   policy (operator chose MIN_CVSS=9.0).
2. Should `finding_filtered` fire per-CVE or per-scan with a count?
   **Per-CVE.** The per-scan summary is already in `scan_integrity`'s
   `n_filtered_min_cvss` bucket; per-CVE audit gives the forensic
   trail when a specific CVE was expected but didn't surface.
3. Restart persistence — new tables vs reuse existing?
   **New tables.** `integrity_state` + `integrity_counters` are
   independent of findings; mixing would couple schema evolution.
4. Snooze expiry TZ?
   **UTC.** Matches audit log + Telegram server. Operators in other
   TZs do the math (same tradeoff beaconry already makes elsewhere).

## Links

- Related: BC-122 (integrity hashes — this story persists the
  counters), BC-104 (pending_notifications — this story adds a
  caller), BC-105 (findings CLI formatter), BC-089 (snooze keyboard).
