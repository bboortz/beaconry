---
id: BC-116
title: Finding state machine extension — add `fixing` + `rolled_back` statuses
status: shipped
created: 2026-11-09
branch: feat/BC-116-finding-state-machine
plan: docs/plan/BC-116-finding-state-machine-plan.md
---

# BC-116: Hub tracks fixes in-flight and reserves rolled-back status

## Narrative

Operator taps [🔧 Fix now] on a CVE. Hub shows `⏳ Fixing…`, runs the
executor (which may retry docker pull for up to ~35s after BC-111).
Pre-BC-116: the DB status stays `open` throughout. If hub crashes
between tap and verify, operator sees `open` forever with no trace
that a fix was mid-flight. If the fix succeeds, status jumps
`open → fixed`. If it fails, same `open`. The `fixing` state —
operator-visible in CLI + CLI-driven dashboards — never existed.

Additionally: `ExecutorPlugin` has no `undo()` method yet (IDEAS.md
line 128), but when it ships we'll need a terminal `rolled_back`
status to distinguish "operator reverted the fix because the service
crashed" from "fix never attempted". BC-116 reserves the status
value now so the future rollback story doesn't need a schema change.

BC-116 adds `fixing` (transient) and `rolled_back` (reserved) to
`_VALID_STATUSES`. `set_status` now validates — a typo in a caller
raises `ValueError` instead of silently writing garbage. `_handle_fix`
transitions `open → fixing` before the executor, and back to `open`
on any non-success terminal (executor fail, non-docker finding,
verify_fix clean=False). On success, the existing `fixed` /
`fixed_verified` path lands. A stale-fixing sweep reverts `fixing`
rows older than `BEACONRY_FIXING_TIMEOUT_S` (default 10 min) back to
`open` and emits a `fix_timeout` audit event.

## Trigger

Any `_handle_fix` invocation, OR any direct `set_status` call from
operator tooling / CLI / test harnesses.

## Expected outcome

- `set_status("fixing")` / `set_status("rolled_back")` succeed.
- `set_status("garbage")` raises `ValueError` with the valid-set hint.
- `_handle_fix` transitions `open → fixing` before `EXECUTOR.execute`.
- Executor-fail or verify-fail reverts `fixing → open`.
- Hub crash mid-fix: stale-fixing sweep reverts after
  `BEACONRY_FIXING_TIMEOUT_S` + fires `fix_timeout` audit.
- `make findings` shows `fixing` in the Status column for in-flight
  fixes (BC-105 formatter already handles arbitrary status strings).

## Out of scope

- `ExecutorPlugin.undo()` + the `fixed → rolled_back` transition —
  separate story (IDEAS.md line 128). BC-116 only reserves the
  status value in `_VALID_STATUSES`.
- `superseded` status (requires tracking which finding supersedes
  which — orthogonal data-model change).
- Formal transition-table enforcement (`fixing → fixed` allowed,
  `fixing → regressed` not allowed). Over-engineered for POC.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Finding state machine extension

  Scenario: fixing + rolled_back are valid statuses
    Given a finding exists
    When set_status is called with "fixing" or "rolled_back"
    Then the row is updated and no exception is raised

  Scenario: set_status rejects an unknown value
    When set_status is called with "garbage_status"
    Then a ValueError is raised
    And the row is NOT modified
    And the error message includes the valid set

  Scenario: Hub crash during fix leaves a stale fixing row
    Given a finding is in status="fixing" with last_status_change_ts
      older than BEACONRY_FIXING_TIMEOUT_S
    When stale_fixing_sweep runs
    Then the finding flips to status="open"
    And a fix_timeout audit event fires

  Scenario: Fresh fixing rows are NOT swept
    Given a finding transitioned to fixing seconds ago
    When stale_fixing_sweep runs
    Then the finding stays in fixing
```

## Capabilities delivered

- **CAP-T218: Finding state machine extension (`fixing` +
  `rolled_back`) + validation + stale sweep.** Operators see
  in-flight fixes; hub crashes don't leave stuck rows; unknown
  status strings surface as errors instead of silent bugs.

## Links

- BC-058 (introduced `set_status` without validation).
- BC-067 audit (origin — "State machine for Finding status",
  IDEAS.md line 200).
- Future: ExecutorPlugin.undo() story will land the
  `fixed → rolled_back` transition that BC-116 reserves.
