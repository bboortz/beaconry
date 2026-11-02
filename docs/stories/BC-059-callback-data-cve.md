---
id: BC-059
title: Telegram callback_data carries CVE id — error can name the CVE
status: shipped
created: 2026-11-02
branch: feat/BC-059-callback-data-cve
plan: docs/plan/BC-059-callback-data-cve-plan.md
---

# BC-059: callback_data carries CVE id

## Narrative

BC-058 made the "Finding not found" Telegram error diagnosable by
including the first 8 chars of the finding_id hash and a stale-
notification hint. But the hash isn't an identifier the operator
recognises — they think in CVE ids, not sha256 prefixes.

The hub already knows the CVE at message-send time; it just doesn't
include it in the button's `callback_data`. Reshape `callback_data`
from `snooze:f0b442fb8acffb61:4` to
`snooze:f0b442fb8acffb61:4:CVE-2026-111497` and the error can say
`⚠️ Snooze failed — CVE-2026-111497 (id f0b442fb…) is not in current DB`.
Finishes the stale-tap story BC-058 opened.

## Trigger

Operator who got BC-058's `"Snooze failed — finding f0b442fb… is not
in the current DB"` would've preferred `"Snooze failed — CVE-2026-111497
is not in the current DB"`.

## Expected outcome

- `callback_data` for Fix/Snooze/Accept/Verify buttons includes the
  CVE id as a trailing segment, under Telegram's 64-byte limit (hard
  cap enforced in a defensive guard).
- `CALLBACK_RE` accepts both the old 3-part form (back-compat for
  messages sent before BC-059 that are still in operators' chat
  history) and the new 4-part form.
- Parser extracts the CVE when present; `on_button_click`'s
  "Finding not found" branch uses it in the error text when available,
  falls back to the id prefix otherwise.
- Idempotence check ("Already snoozed", "Already accepted") uses the
  CVE when available for the user-facing text (today it already does
  via `finding.cve_id` after a successful load).

## Out of scope

- Changing anything the HTTP CLI side (`make set-status`) renders.
- Carrying both the primary id AND the KEV cve_alias in callback_data.
  Only one CVE field; the primary id goes in. BC-043 alias enrichment
  stays on the display side.
- Reshaping the first-fire notification body itself.

## Acceptance criteria (Gherkin)

```gherkin
Feature: callback_data carries CVE id

  Scenario: Fix button carries CVE in callback_data
    Given a finding with cve_id "CVE-2026-111497"
    When _kb_first_fire builds its inline keyboard
    Then the Fix button's callback_data ends with ":CVE-2026-111497"

  Scenario: Snooze button carries CVE after the hours segment
    Given a finding with cve_id "CVE-2026-111497"
    When _kb_first_fire builds its inline keyboard
    Then the Snooze button's callback_data is "snooze:<id>:4:CVE-2026-111497"

  Scenario: Verify button carries CVE
    Given a finding with cve_id "CVE-2026-111497"
    When the verify keyboard is built after a successful fix
    Then the Verify button's callback_data ends with ":CVE-2026-111497"

  Scenario: Old 3-part callback_data still parses
    Given a Telegram callback with data "snooze:f0b442fb8acffb61:4"
    When CALLBACK_RE matches
    Then the match succeeds (back-compat with messages sent before BC-059)

  Scenario: New 4-part callback_data parses with CVE
    Given a Telegram callback with data "snooze:f0b442fb8acffb61:4:CVE-2026-111497"
    When CALLBACK_RE matches
    Then the match succeeds
    And on_button_click's parsed cve equals "CVE-2026-111497"

  Scenario: Finding not found error includes CVE when available
    Given a Telegram callback with cve "CVE-2026-111497" and a finding_id not in the DB
    When the handler processes it
    Then the message mentions "CVE-2026-111497"

  Scenario: Finding not found error falls back to id prefix when CVE absent
    Given a Telegram callback in the old 3-part form (no CVE) with id not in DB
    When the handler processes it
    Then the message includes the 8-char id prefix

  Scenario: callback_data stays under 64 bytes
    Given any Finding
    When _cb_data is called for any action
    Then len(callback_data.encode()) <= 64
```

## Capabilities delivered

- **CAP-T118: callback_data reshape with CVE suffix + 64-byte guard** (technical).
- **CAP-T119: Backward-compatible CALLBACK_RE accepts both 3-part and 4-part** (technical).
- **CAP-B042: Operator sees the CVE in the Telegram "Finding not found"
  error instead of a hex prefix** (business).

## Links

- Depends on: BC-058 (the diagnosable-error machinery this story completes).
- Follow-up: BC-059C (`notify_sent_failed` audit symmetry).
