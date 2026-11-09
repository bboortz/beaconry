---
id: BC-115
title: Audit write failure sticky flag — surface disk-full degradation
status: shipped
created: 2026-11-09
branch: feat/BC-115-audit-sticky-flag
plan: docs/plan/BC-115-audit-sticky-flag-plan.md
---

# BC-115: `/healthz` surfaces when the audit log fails to write

## Narrative

Operator's hub is in the middle of an incident — a scout just fired
a batch of KEV CVEs that triggered 20 Telegram alerts, button taps
are flowing, and the audit log is the forensic record of what the
system did. Then `/data` fills up (log rotation forgot to run, grype
DB grew unexpectedly, etc.). Pre-BC-115: `audit.append()` catches
the OSError, logs `audit write failed: No space left on device`,
and silently drops the event. The operator continues tapping
buttons, status transitions happen, but nothing is persisted to
audit.log. Weeks later, during a post-incident review, the audit
trail has a gaping hole and no indication of why.

BC-115 preserves the never-raise contract but adds a sticky
degraded-mode flag + cumulative failure counter + last-error string.
`/healthz` surfaces all three. Operators monitoring hub via
`make status` see `audit_degraded: true` the moment the log breaks;
the counter stops climbing once the disk is cleared; a successful
write flips the flag back. The `audit_write_failures` counter stays
cumulative so operators see historical degradation even after
recovery.

## Trigger

Any `OSError` raised by the `path.open(...).write(...)` inside
`audit.append()` — disk full, permission denied, file deleted,
read-only mount.

## Expected outcome

- `audit.append()` continues to never raise.
- Module-level `_degraded` flag flips True on write failure.
- `_last_error` carries the exception type + message (truncated to
  200 chars).
- `_write_failures` cumulative counter increments on every failure.
- First successful write after degradation clears the sticky flag
  and the last_error; the cumulative counter persists.
- `/healthz` checks block includes `audit_degraded`,
  `audit_write_failures`, `audit_last_error`.
- Audit values are excluded from the aggregate `degraded` status —
  rationale: a transient disk full shouldn't cascade to
  "hub-wide degraded" and trip compose unhealthy → scout
  `depends_on: service_healthy` → fleet outage. Operators see the
  degradation directly in the checks block.

## Out of scope

- Audit-log rotation / purging (orthogonal — ops concern).
- Alternative sinks (shipping audit to syslog / remote collector).
- Alerting on degraded audit (future BC — operator sees via
  `make status` today).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Audit log surfaces write-failure degradation without crashing

  Scenario: Fresh module reports clean state
    Given no audit writes have occurred
    When /healthz checks are read
    Then audit_degraded is False
    And audit_write_failures is 0
    And audit_last_error is ""

  Scenario: Write failure sets sticky flag + does not raise
    Given the audit file cannot be opened
    When audit.append("event") is called
    Then no exception propagates to the caller
    And audit_degraded becomes True
    And audit_write_failures becomes 1
    And audit_last_error contains the exception class + message

  Scenario: Recovery clears the sticky flag but keeps cumulative counter
    Given audit was degraded after 2 failed writes
    When a subsequent write succeeds
    Then audit_degraded becomes False
    And audit_last_error becomes ""
    And audit_write_failures stays 2 (cumulative, intentional)

  Scenario: Aggregate /healthz degraded status is NOT forced by audit
    Given audit_degraded is True
    When /healthz is read
    Then the audit values are visible in checks
    But the global "status" field is NOT automatically "degraded"
```

## Capabilities delivered

- **CAP-T217: Audit log sticky degraded-mode flag.** Operators see
  silent audit-write loss (disk full, perms, missing mount) in
  `/healthz` without any change to the fire-and-forget contract.

## Links

- BC-029 (introduced the audit log + fire-and-forget semantics).
- BC-067 audit (origin — "Audit log OSError silently swallowed on
  disk full", IDEAS.md line 273).
