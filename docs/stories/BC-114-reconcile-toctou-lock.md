---
id: BC-114
title: Reconcile TOCTOU lock — serialize concurrent writes during auto-close
status: in-progress
created: 2026-11-09
branch: feat/BC-114-reconcile-toctou-lock
plan: docs/plan/BC-114-reconcile-toctou-lock-plan.md
---

# BC-114: Reconcile wraps its SELECT + UPDATEs in BEGIN IMMEDIATE

## Narrative

Two scouts post `/scan` to hub within the same ~100ms window — common
in a fleet where cron jobs align. Scout A's processing path does
`bump_last_seen` (resets absent_count=0 for scanned ids) followed by
`reconcile_scan` (bumps absent_count for host's absent ids). Mid-way
through reconcile — after it SELECTs rows but before its per-row
UPDATE — scout B's `save_finding` runs and sets `absent_count=0` on
finding F because B's scan saw F. Reconcile then UPDATEs F with the
stale pre-save absent_count+1, auto-closing F even though B just
observed it present. False auto-close; operator thinks a real CVE is
gone.

BC-114 wraps reconcile's SELECT + UPDATEs in a BEGIN IMMEDIATE
transaction. Concurrent writers (save_finding, bump_last_seen) block
on the write lock until reconcile COMMITs — serialization guarantees
no stale read-modify-write. If another writer holds the lock past
the 5s SQLite default, reconcile logs a warning and returns empty —
the next cycle retries.

## Trigger

Any two concurrent scans on the same host whose processing paths
hit the hub within the reconcile's SELECT → UPDATE window.

## Expected outcome

- Reconcile acquires `BEGIN IMMEDIATE` at the start of its SELECT.
- Concurrent writers block until reconcile COMMITs.
- If the lock cannot be acquired (another writer holds it), reconcile
  skips the cycle and logs `BC-114: reconcile_scan could not acquire
  write lock`. No partial writes, no silent auto-close misfires.
- All pre-existing reconcile behavior preserved — auto-fix flip,
  regression detection, absent-count bumps.

## Out of scope

- Serializing save_finding against itself (single statement, already
  atomic in SQLite).
- Lock timeout tuning (SQLite's default 5s is fine for POC scale).
- Lock-acquisition retry loop (next reconcile cycle retries naturally
  via the scan cadence).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Reconcile serializes against concurrent writers

  Scenario: Existing auto-close path still works
    Given a finding with absent_count = THRESHOLD - 1
    And the finding is absent from the current scan
    When reconcile_scan runs
    Then the finding flips to auto_fixed
    And absent_count equals THRESHOLD

  Scenario: Lock unavailable → skip cycle
    Given another writer holds BEGIN EXCLUSIVE
    When reconcile_scan runs with a short timeout
    Then it returns ([], [], [])
    And no rows are modified
    And the warning "could not acquire write lock" is logged

  Scenario: BEGIN IMMEDIATE wraps SELECT + UPDATEs
    When reconcile_scan runs
    Then isolation_level is set to None on its connection
    And BEGIN IMMEDIATE is executed before any SELECT
    And COMMIT is executed after all UPDATEs
```

## Capabilities delivered

- **CAP-T216: Reconcile TOCTOU serialization via BEGIN IMMEDIATE.**
  Closes the read-modify-write race between reconcile_scan and
  concurrent save_finding / bump_last_seen — no more false
  auto-closes under fleet-wide cron alignment.

## Links

- BC-086 (introduced counter-based auto-fix — the write path that
  BC-114 now serializes).
- BC-067 audit (origin — "TOCTOU race: reconcile_scan vs. concurrent
  save_finding", IDEAS.md line 278).
