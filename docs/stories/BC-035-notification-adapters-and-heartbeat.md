---
id: BC-035
title: Notification adapter architecture + heartbeat for stale open findings
status: shipped
created: 2026-10-31
branch: feat/BC-035-notification-adapters-and-heartbeat
plan: docs/plan/BC-035-notification-adapters-and-heartbeat-plan.md
---

# BC-035: Notification adapter architecture + heartbeat for stale open findings

## Narrative

Operator ask 2026-10-31: *"important is that we can enable/disable all kind of notification and the notification types are following an adapter approach. so that we are easily able to add/remove notification targets like telegram, email, reddit, etc."* The hub's outbound notifications were tightly coupled to `tg_app.bot.send_message` — adding a second channel (email, Reddit, webhook) would mean touching every call site, and operators had no way to silence just one channel.

Second half of the story — the "fix is not forget" surveillance principle: once BC-034 landed `last_seen_ts`, we finally have the data to notice *silent-forever* findings. A row sitting at `status=open` could be an operator's forgotten TODO from four months ago. BC-035 adds a periodic heartbeat sweep that re-pokes stale open findings through the same adapter registry — so disabling Telegram also silences heartbeats, and tomorrow's email adapter inherits the behavior for free.

## Trigger

- Any outbound notification (CVE finding, regression, digest, heartbeat).
- Internal sweep: every `BEACONRY_HEARTBEAT_SWEEP_INTERVAL_S` (default 1h), hub walks the DB for stale open findings.

## Expected outcome

- All four outbound notification kinds (finding, regression, heartbeat, digest) dispatch through a registry of `NotificationAdapter` subclasses.
- Each adapter has an independent `env_var` enable flag; operator can disable Telegram without disabling the whole system.
- Adding a new channel (email, Reddit, SMS, webhook) is one new file + one `register(...)` call in lifespan — no changes to call sites.
- Open findings not touched in `HEARTBEAT_INTERVAL_S` (default 7d) and still in inventory (`last_seen_ts` within liveness window) get a reminder via `send_heartbeat`.
- `last_notified_ts` advances after each reminder so the sweep self-rate-limits.
- Operator can opt out entirely with `BEACONRY_HEARTBEAT_ENABLED=0`.

## Out of scope

- Second concrete adapter (email, Reddit, …): adapter *interface* ships; implementations come in follow-up stories.
- Per-finding mute (vs. per-channel disable): operators can snooze/accept today; a fine-grained per-finding channel preference is a future story.
- Interactive callbacks (button taps): stay Telegram-specific in `on_button_click`. The adapter abstraction is for OUTBOUND dispatch only.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Notification adapter architecture

  Scenario: Single adapter receives dispatch
    Given one adapter is registered
    When notify_finding is called with a finding
    Then that adapter's send_finding receives the finding

  Scenario: Multiple adapters all receive dispatch
    Given two adapters are registered
    When notify_finding is called
    Then both adapters' send_finding receive the finding

  Scenario: Disabled adapter is skipped
    Given adapter A has env_var "BEACONRY_NOTIFIER_A_ENABLED"
    And BEACONRY_NOTIFIER_A_ENABLED=0
    When notify_finding is called
    Then A.send_finding is NOT called

  Scenario: Failing adapter does not block siblings
    Given adapter A raises in send_finding
    And adapter B is also registered
    When notify_finding is called
    Then A sees the call and raises, B still receives dispatch, no exception propagates

  Scenario: All four dispatch kinds reach each adapter
    When notify_finding, notify_regression, notify_heartbeat, notify_digest are called
    Then the adapter records exactly those four method calls in order

Feature: Heartbeat sweep for stale open findings

  Scenario: Open finding past interval gets reminded
    Given finding F has status=open, last_seen_ts=now, last_notified_ts=now-10d
    And HEARTBEAT_INTERVAL_S=7d
    When heartbeat_sweep runs
    Then exactly one heartbeat is dispatched for F
    And F.last_notified_ts is advanced to ~now

  Scenario: Recently notified finding is skipped
    Given F.last_notified_ts=now-2d, interval=7d
    When heartbeat_sweep runs
    Then no reminder is sent

  Scenario: Non-open statuses are skipped
    Given F is snoozed OR accepted OR fixed
    When heartbeat_sweep runs
    Then no reminder is sent

  Scenario: Finding absent from inventory is not reminded
    Given F.last_seen_ts < now - HEARTBEAT_LIVENESS_WINDOW_S
    When heartbeat_sweep runs
    Then no reminder (BC-031 reconcile will auto-close it)

  Scenario: Operator opt-out
    Given BEACONRY_HEARTBEAT_ENABLED=0
    When heartbeat_sweep runs
    Then it is a no-op even with stale findings

  Scenario: Audit log records reminders
    Given stale open finding F
    When heartbeat_sweep sends the reminder
    Then audit.log carries one "heartbeat_reminder_sent" event with cve_id, finding_id, days_open

  Scenario: Cold-boot safety
    Given the findings table does not exist
    When heartbeat_sweep is called
    Then no exception; function returns 0
```

## Capabilities delivered

- **CAP-T078: Notification adapter architecture with per-channel enable/disable** (technical, active) — registry + ABC + per-adapter env flag; Telegram is the first concrete adapter.
- **CAP-T079: Heartbeat sweep reminds operators about stale open findings** (technical, active) — periodic sweep over `status=open` rows past interval, dispatched through the adapter registry.

Extends: CAP-T077 (last_seen_ts, BC-034). Related: CAP-B003 (notification surface), CAP-T071 (reconcile — same data).

## Links

- Design: `DESIGN.md § Notifications`
- Related stories: BC-034 (last_seen_ts prerequisite), BC-031 (reconcile — complementary auto-close path), BC-029 (audit log).
- Enables (future BCs): email adapter, Reddit adapter, SMS adapter, webhook adapter — each one new file + one `register(...)` call.
