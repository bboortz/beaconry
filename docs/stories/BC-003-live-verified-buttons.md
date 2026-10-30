---
id: BC-003
title: Live-verified Telegram button handling
status: shipped
created: 2026-10-29
---

# BC-003: Live-verified Telegram button handling

## Narrative

Same operator from BC-002: five real CVE notifications on their phone, tap-worthy buttons. They tap Snooze on the first, Accept on the second, Fix on the third. Each message edits in-place to confirm the action. When they Ctrl-C the demo and run `make demo` again, the three actioned findings don't re-notify — the pipeline respects the operator's prior decisions. BC-002 shipped the notification half; BC-003 proves the operator-interaction half works end-to-end on real Telegram with real taps.

## Trigger

Operator sees notifications from a running `make demo` session (not one that already exited) and taps buttons on their phone.

## Expected outcome

- **Tap [⏰ Snooze 4h]** → message edits to "⏰ Snoozed 4h — `<CVE>`"; dev DB status for that finding = `snoozed`
- **Tap [✅ Accept risk]** → message edits to "✅ Risk accepted — `<CVE>`"; DB status = `accepted`
- **Tap [🔧 Fix now]** (`BEACONRY_FIX_ENABLED` unset) → message edits to "⚙️ Fix execution coming in BC-004 — `<CVE>` left as `open`"; DB status unchanged
- Subsequent `make demo` run **does not re-notify** findings whose status is `snoozed` / `accepted` / `fixed`
- Double-taps are safe — second tap finds the status already changed and no-ops with a hint
- `pytest -m real_tools` actually passes with real syft + real grype
- README documents `make demo` operator setup
- Dev DB filename reconciled across story + code (either `dev.db` everywhere or `beaconry.db` everywhere; pick one in the plan doc)

## Out of scope

- Fix-now actually executing fix (will become BC-004)
- Snooze expiration (just stores status; no cron)
- Multi-finding batch actions ("Snooze all")
- Buttons on the digest message
- Authn/authz on the bot (only the configured chat_id can tap for now)
- Reminders / escalation if operator doesn't tap

## Acceptance criteria (Gherkin)

```gherkin
Feature: Live-verified Telegram button handling

  Background:
    Given BC-002 shipped — make demo is sending real notifications
    And the demo session is actively running (not just one-shot pipeline)
    And at least one finding has arrived with inline buttons

  Scenario: Snooze button updates DB and edits message (manual-verified)
    When the operator taps [⏰ Snooze 4h] on a finding message
    Then the message is edited to "⏰ Snoozed 4h — <cve>"
    And the dev DB shows status="snoozed" for that finding
    And on the next make demo run the finding is NOT re-notified

  Scenario: Accept-risk button updates DB and edits message (manual-verified)
    When the operator taps [✅ Accept risk] on a finding message
    Then the message is edited to "✅ Risk accepted — <cve>"
    And the dev DB shows status="accepted" for that finding
    And on the next make demo run the finding is NOT re-notified

  Scenario: Fix-now stub honored (manual-verified)
    Given BEACONRY_FIX_ENABLED is unset
    When the operator taps [🔧 Fix now] on a finding message
    Then the message is edited to "⚙️ Fix execution coming in BC-004 — <cve> left as open"
    And the dev DB status remains "open"
    And no docker subprocess is spawned

  Scenario: Double-tap is idempotent (manual-verified)
    Given a finding whose status is already "snoozed"
    When the operator taps [⏰ Snooze 4h] again
    Then the handler either (a) no-ops with the current status visible,
         or (b) edits the message to say "Already snoozed"
    And the DB status does not flip to anything invalid

  Scenario: Callback data validation
    Given a crafted callback with data not matching {fix|snooze|ack}:<16-hex>(:<int>)?
    When the Telegram client (or test harness) delivers it
    Then the handler responds with an "invalid request" edit
    And no DB mutation occurs
    And a warning is logged

  Scenario: real_tools integration test passes
    When "pytest -m real_tools" is invoked by a developer
    Then it exercises the real syft + real grype path against nginx:1.24
    And asserts findings are produced with the expected schema
    And exits 0 within 60 seconds

  Scenario: README documents operator setup
    Given a fresh clone of the repo
    When a new developer reads README.md
    Then it names: make demo prerequisites, .env setup pointer, the
         --self-test flag, the BEACONRY_MOCK fallback, and the per-run
         cap env

  Scenario: Dev DB filename is consistent story-to-code
    Given the story Gherkin references the dev DB path
    When "grep -r '.beaconry-cache/' docs/ poc/" is run
    Then every occurrence names the same filename (dev.db OR beaconry.db — one choice, applied consistently)
```

## Verification protocol

Live scenarios (Snooze / Accept / Fix / double-tap) require a manual run with user + Claude:

1. Claude starts `make demo` in a long-lived background shell
2. User taps each button on their phone (one message at a time)
3. Claude confirms via `.beaconry-cache/*.db` SQLite inspection
4. User reports back; Claude records each as pass/fail
5. Only once all four live scenarios pass does the story flip to shipped

The non-live scenarios (callback validation, pytest -m real_tools, README, DB-filename reconciliation) are standard automated/review work.

## Capabilities delivered

*To be finalised when BC-003 moves `draft → ready`. Preliminary list:*

- **CAP-B007: Operator snoozes/accepts findings and the system respects the decision across runs** (business, aspirational → active on ship) — new.
- **CAP-B008: Fix-now button is honest about its status (stub until BC-004-successor)** (business, aspirational → active on ship) — new.
- **CAP-T018: Callback_data validation rejects malformed input** (technical, aspirational) — new.
- **CAP-T019: Button handlers are idempotent on double-tap** (technical, aspirational) — new.

Depends on CAP-B002 (notifications arrive — prerequisite), CAP-T003 (HTML escape in edited messages).

## Shipping evidence (shipped — see plan for the full table)

Phase 1+2 (automated) and Phase 3 (live tap session on 2026-10-30) all closed. See `docs/plan/BC-003-live-verified-buttons-plan.md` for the complete Shipping evidence table.

## Open questions

- Should Fix-now now point to BC-004 or stay at BC-003 if we re-scope? → Settle in the plan doc when BC-003 moves `draft → ready`.
- Idempotence design: no-op silently vs. re-edit "already actioned" message? → Pick in plan doc; default: re-edit with current status.

## Links

- BC-002 (notification side — this story's prerequisite)
- Design: `DESIGN.md § Notification format`, `memory/telegram-patterns.md § Callback handler`
