---
id: BC-060
title: notify_sent_failed audit symmetry
status: shipped
created: 2026-11-02
branch: feat/BC-060-notify-sent-failed
plan: docs/plan/BC-060-notify-sent-failed-plan.md
---

# BC-060: notify_sent_failed audit symmetry

## Narrative

BC-058 added `notify_sent` audit events on every successful Telegram
dispatch so stale-tap debugging became possible. But if a Telegram
`send_message` call *fails* — token revoked (401), network drops, rate
limit kicks in (429) — the hub currently just logs a WARNING and the
operator has no durable record. Hub logs scroll off; the audit log
survives.

Mirror BC-058's success events with `notify_sent_failed` carrying a
`reason` string. Operator can grep `make audit EVENT=notify_sent_failed`
and know exactly which notifications never landed.

## Trigger

Operator rotates the Telegram token. Hub keeps running. For the hour
before the operator notices, every notification silently 401s and
disappears. Nothing in the audit log explains the gap.

## Expected outcome

- Each of the four dispatch sites (`notify`, `notify_regression`,
  `notify_heartbeat`, `record_digest_sent`/`send_digest`) catches
  exceptions from `_dispatch_*` and appends `notify_sent_failed` with
  `{finding_id, cve_id, host, kind, reason}` (or digest-shape for the
  digest site).
- The exception still propagates so callers' existing error paths
  (e.g. `process_sbom_entry`'s `log.warning(...)`) stay triggered.
- `reason` is the exception class name + a truncated message — enough
  for forensic grep, not enough to leak secrets.

## Out of scope

- Retry logic. One send attempt per finding; retry policy is a future
  BC if/when it becomes needed.
- Email/Slack/Reddit adapters' per-channel failures. The dispatcher
  fans out to all enabled adapters; if any one raises, the whole
  dispatch raises. Per-channel audit granularity is a bigger refactor.
- `set_status` failures (SQLite errors). The DB write is local; its
  failure modes are different.

## Acceptance criteria (Gherkin)

```gherkin
Feature: notify_sent_failed audit symmetry

  Scenario: notify failure emits notify_sent_failed
    Given _dispatch_finding raises urllib.error.HTTPError(401)
    When notify(f) is awaited
    Then an audit event is appended with
      {event: "notify_sent_failed", finding_id: f.id, cve_id: f.cve_id,
       host: f.host, kind: "finding", reason: "HTTPError:..."}
    And the exception propagates to the caller

  Scenario: notify_regression failure emits notify_sent_failed
    Given _dispatch_regression raises on the finding f
    When notify_regression(f, …) is awaited
    Then audit event is appended with kind="regression"

  Scenario: notify_heartbeat failure emits notify_sent_failed
    Given _dispatch_heartbeat raises on the finding f
    When notify_heartbeat(f, …) is awaited
    Then audit event is appended with kind="heartbeat"

  Scenario: digest send failure emits notify_digest_sent_failed
    Given _dispatch_digest raises
    When send_digest(suppressed) is awaited
    Then audit event is appended with
      {event: "notify_digest_sent_failed", finding_ids: [...],
       count: n, reason: "..."}
    And the exception propagates

  Scenario: reason doesn't include full exception message
    Given _dispatch_finding raises with a long error message
    When notify(f) is awaited
    Then the audited reason is truncated to ≤ 200 chars
```

## Capabilities delivered

- **CAP-T120: notify_sent_failed + notify_digest_sent_failed audit events** (technical).
- **CAP-B043: Operator can see which notifications failed in the audit
  log** (business).

## Links

- Depends on: BC-058 (notify_sent + record_digest_sent).
