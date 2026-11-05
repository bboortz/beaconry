---
id: BC-089
title: Snooze button with real expiry — [⏰ Nh] actually re-opens after Nh
status: in-progress
created: 2026-11-05
branch: feat/BC-089-snooze-expiry
plan: docs/plan/BC-089-snooze-expiry-plan.md
---

# BC-089: Snooze with real expiry

## Narrative

Operator looks at a Tier-2 finding while in a meeting, decides it's
not urgent, taps [⏰ Snooze 4h]. Beaconry replies "Snoozed 4h —
CVE-… will come back at 14:30". Four hours later the operator expects
a fresh notification; nothing arrives. The finding is still snoozed
— because today's implementation parses the `4` in the callback_data,
prints it in the reply, but **never stores it**. The label lies.

BC-089 makes the label truthful. New column `snooze_until_ts`, periodic
sweep re-opens expired rows, re-fires notification (opt-out via env).
Keyboard also expands from one [⏰ Snooze 4h] button to four
configurable durations.

## Trigger

Operator taps any snooze button on a finding notification (new,
regression, heartbeat, or auto-fix regression).

## Expected outcome

- At tap time: finding row stores `snooze_until_ts = now + hours*3600`;
  Telegram reply confirms "…will come back at <HH:MM>" with real time,
  not "4h" placeholder.
- At expiry: sweep flips `snoozed → open`, audit `snooze_expired`,
  fires `notify_snooze_expired()` (distinct body: "⏰ Snooze expired
  — this finding is back on your radar") unless operator opted out.
- Operator can configure default snooze durations in the keyboard
  via `BEACONRY_SNOOZE_DEFAULT_HOURS` (default `1,4,24,168`).
- Pre-BC-089 snoozes (snooze_until_ts = 0 default) stay snoozed
  indefinitely — no surprise re-open on upgrade.

## Out of scope

- Snooze-until-specific-time (e.g. "snooze until Monday 09:00"). v1
  is relative-hours only.
- Snooze-per-tier auto-suggestion (e.g. "Tier 2 default snooze = 1h").
  Operator picks a button; beaconry doesn't.
- Rolling snooze (tap again to extend). First tap wins; operator who
  wants longer hits a different button before expiry or re-snoozes
  after.

## Acceptance criteria (Gherkin)

```gherkin
Feature: snooze with real expiry

  Scenario: Snooze tap stores expiry timestamp
    Given a finding with status `open`
    When the operator taps [⏰ Snooze 4h] at 10:00
    Then the DB row has status `snoozed`
      And snooze_until_ts = 10:00 + 4h = 14:00 (unix ts)
      And the Telegram reply names the expiry time

  Scenario: Sweep re-opens expired snooze
    Given a finding snoozed at 10:00 with snooze_until_ts = 14:00
    When the sweep runs at 14:01
    Then the finding's status flips to `open`
      And an audit `snooze_expired` event is written
      And notify_snooze_expired() fires (⏰ Snooze expired body)

  Scenario: Sweep ignores not-yet-expired snoozes
    Given a finding snoozed with snooze_until_ts = 14:00
    When the sweep runs at 13:59
    Then the finding stays `snoozed`

  Scenario: Legacy snooze (pre-BC-089) stays indefinite
    Given a finding with status `snoozed` and snooze_until_ts = 0
    When the sweep runs
    Then the finding stays `snoozed`
      And no audit event is written for this row

  Scenario: Multiple snooze buttons on the keyboard
    Given BEACONRY_SNOOZE_DEFAULT_HOURS=1,4,24,168
    When a new finding fires notify()
    Then the inline keyboard shows [⏰ 1h] [⏰ 4h] [⏰ 1d] [⏰ 1w]
      And [🔧 Fix now] [✅ Accept risk] remain

  Scenario: Opt out of expiry re-notification
    Given BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=0
    When the sweep re-opens an expired snooze
    Then the status flips and audit fires
      But notify_snooze_expired() is NOT called
```

## Capabilities delivered

- **CAP-T183: snooze_until_ts column + sweep + notify_snooze_expired**
  (type: technical, status: active)
  *Schema v6 adds `snooze_until_ts INTEGER DEFAULT 0`. New periodic
  task `snooze_expiry_sweep` flips `snoozed → open` at expiry and
  optionally fires distinct notification.*
- **CAP-T184: configurable snooze durations in keyboard**
  (type: technical, status: active)
  *`BEACONRY_SNOOZE_DEFAULT_HOURS` env lists integer hours that render
  as [⏰ Nh] buttons on every snooze-eligible notification. Default
  `1,4,24,168`.*

Extends: CAP-T105 (inline keyboard architecture), CAP-T029 (audit event
on state change).

## Links

- `docs/LIFECYCLE.md § 17.1` (once BC-088 merges — the bug this fixes)
- `docs/plan/IDEAS.md` entry "Snooze 4h button doesn't actually snooze"
