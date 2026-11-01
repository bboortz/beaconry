---
id: BC-058
title: Fix mutation + audit landmines (set_status rowcount, audit shape, notify_sent, Telegram error)
status: shipped
created: 2026-11-01
branch: feat/BC-058-mutation-and-audit-cleanup
plan: docs/plan/BC-058-mutation-and-audit-cleanup-plan.md
---

# BC-058: Mutation + audit cleanup

## Narrative

Debugging the operator's "Finding not found or already resolved"
Telegram error surfaced four interacting bugs:

1. `set_status()` silently no-ops when the row doesn't exist and emits
   an audit event anyway — a correctness landmine for every future
   caller.
2. Audit `status_change` events have asymmetric shapes between the
   Telegram callback path (minimal: `{finding_id, status}`) and the
   BC-055 PATCH path (rich: `{finding_id, host, cve_id, old_status,
   new_status, source}`). `make audit` enrichment behaves differently
   per source.
3. Hub never emits a `notify_sent` audit event — there's no durable
   record linking a Telegram message to the finding_id that was in its
   `callback_data`. Debugging a stale-notification tap is impossible.
4. The "Finding not found" message is misleading ("or already
   resolved" implies a state check that doesn't exist) and
   undiagnosable (no finding_id prefix, no hint).

Drive-by:

5. `is_multi_host_inventory()` (BC-057) re-opens a sqlite connection
   on every `format_notification` call. In digest mode that's dozens of
   needless opens per render. Memoize per-call batch.

## Trigger

Observed when operator tapped Snooze on a Telegram notification whose
`finding_id` wasn't in the current DB — got the misleading error with
no way to tell which notification was stale.

## Expected outcome

- `set_status()` raises `FindingNotFound` when the row doesn't exist.
  Callers already protected by `load_finding` are unaffected; the
  function stops being a footgun for future callers.
- `set_status()` audit event matches the BC-055 PATCH shape:
  `{finding_id, host, cve_id, old_status, new_status, source: "telegram"}`.
- A new `notify_sent` audit event fires after every successful
  `_dispatch_finding` / `_dispatch_heartbeat` / `_dispatch_regression`.
  Shape: `{finding_id, cve_id, host, kind: "finding"|"heartbeat"|"regression"}`.
  Digest emits a single `notify_digest_sent` with the list of
  suppressed finding_ids.
- Telegram "Finding not found" error includes the finding_id prefix +
  a hint about stale notifications. New wording: `"⚠️ Finding X (id
  f0b442f…) not in current DB — likely an old notification from before
  a scan reset. Safe to ignore."`.
- `format_notification` accepts an optional `multi_host` hint; the
  dispatcher computes it once per batch and passes it through.
  Backward-compat: when the hint is `None`, falls back to the DB
  lookup (today's behavior).

## Out of scope

- Full retrofit of audit shape across all mutation sites in the
  codebase. Only `set_status` and the four notify paths.
- Changing the Telegram `callback_data` format to carry the CVE id
  (requires message-send-side change + ≤64-byte constraint). Follow-up.
- The syft/hub POST timeouts bumped to 1800s on scout. Separate
  conversation per operator's call.
- `make scan` container-name hardcoding. Separate story.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Mutation + audit cleanup

  Scenario: set_status on unknown finding raises FindingNotFound
    Given no finding with id "nope-x" in the DB
    When set_status("nope-x", "snoozed") is called
    Then FindingNotFound is raised
    And no audit event is written

  Scenario: set_status on known finding emits rich audit event
    Given a finding with id "fid-1", host "laptop", cve_id "CVE-X", status "open"
    When set_status("fid-1", "snoozed", source="telegram") is called
    Then the DB row's status becomes "snoozed"
    And an audit event is appended with
      {finding_id: "fid-1", host: "laptop", cve_id: "CVE-X",
       old_status: "open", new_status: "snoozed", source: "telegram"}

  Scenario: notify_sent audit event fires on finding dispatch
    Given a Finding f with id "x", cve_id "CVE-X", host "laptop"
    When notify(f) completes successfully
    Then an audit event is appended with
      {event: "notify_sent", finding_id: "x", cve_id: "CVE-X",
       host: "laptop", kind: "finding"}

  Scenario: notify_digest_sent audit event lists suppressed finding_ids
    When _dispatch_digest(suppressed_list_of_3) completes
    Then an audit event is appended with
      {event: "notify_digest_sent", finding_ids: [...3 ids...]}

  Scenario: Telegram "Finding not found" error includes id + hint
    Given a Telegram callback with finding_id "aaaaaaaaaaaaaaaa" (not in DB)
    When the handler processes it
    Then the message is edited to include "aaaaaaaa…" (first 8 chars)
    And includes the phrase "old notification" or "stale notification"

  Scenario: format_notification doesn't re-query DB when hint is passed
    Given format_notification is called with multi_host=True
    Then is_multi_host_inventory is NOT called
    And the rendered message leads with the host prefix
```

## Capabilities delivered

- **CAP-T115: set_status fail-fast + rich audit shape** (technical).
- **CAP-T116: notify_sent + notify_digest_sent audit events** (technical).
- **CAP-T117: Diagnosable Telegram "Finding not found" error** (technical).
- **CAP-B041: Operator can diagnose stale-notification taps + has
  consistent audit coverage across every mutation path** (business).

## Links

- Related: BC-055 (PATCH /findings path with the rich audit shape BC-058
  retrofits into the Telegram path), BC-057 (`is_multi_host_inventory`
  memoization drive-by).
