---
id: BC-090
title: Auto-fix regression body actually reaches Telegram
status: in-progress
created: 2026-11-05
branch: feat/BC-090-auto-fix-regression-body
plan: docs/plan/BC-090-auto-fix-regression-body-plan.md
---

# BC-090: Wire `format_auto_fix_regression` through the real Telegram path

## Narrative

An operator who auto-snoozed a finding sees it silently flip to
`auto_fixed` (BC-086 — intended). Days later, the CVE comes back:
reconcile flips `auto_fixed → regressed_from_auto_fix`. Hub fires
`notify_auto_fix_regression`. Operator expects to see the distinct
🔥 **AUTO-FIX REGRESSED** body designed in BC-086 CAP-T178 — a
clear signal that "system-inferred close was wrong; CVE is actively
back".

What they actually see on Telegram is the **BC-031 generic 🔥 FIX
REGRESSED** body. The distinct auto-fix body is dead code on the
real path. Discovered during BC-088 lifecycle authoring (§15.1):
`notify_auto_fix_regression` uses `format_auto_fix_regression`
only on the MOCK path; the real path calls `_dispatch_regression`
→ `TelegramAdapter.send_regression` → `format_regression`.

BC-090 adds `send_auto_fix_regression` to the adapter protocol,
hooks TelegramAdapter to it, and swaps the dispatch target.

## Trigger

An `auto_fixed` finding reappears in a scan; reconcile flips it to
`regressed_from_auto_fix` and calls `notify_auto_fix_regression`.

## Expected outcome

- Telegram body carries the 🔥 **AUTO-FIX REGRESSED** header +
  "auto-fixed Xd ago, now back" context, not the generic regression
  body.
- Audit still records `regressed_from_auto_fix` (unchanged).
- Operator can opt out of the loud alert via
  `BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED=0` — status still
  flips, audit still fires, but no Telegram ping. Rare escape hatch
  for operators who want absolute silence around auto-fix cycles.

## Out of scope

- Reworking `regressed_from_auto_fix` into its own status with
  dedicated keyboard. Keyboard remains the regression keyboard
  (Fix / Accept, no Snooze).
- Adding per-regression-kind config toggles beyond the one above.

## Acceptance criteria (Gherkin)

```gherkin
Feature: auto-fix regression body actually reaches Telegram

  Scenario: Telegram adapter renders the distinct body
    Given a finding transitions auto_fixed → regressed_from_auto_fix
    When notify_auto_fix_regression dispatches
    Then TelegramAdapter.send_auto_fix_regression is invoked
     And the rendered body contains "🔥 AUTO-FIX REGRESSED"
     And it does NOT contain the generic "FIX REGRESSED" header

  Scenario: Opt out suppresses the Telegram ping
    Given BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED=0
    When notify_auto_fix_regression fires
    Then no adapter.send_* is invoked
     And audit `regressed_from_auto_fix` still fires
     And status still flips to `regressed`

  Scenario: Default behaviour is loud
    Given BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED is unset
    When notify_auto_fix_regression fires
    Then TelegramAdapter.send_auto_fix_regression IS invoked

  Scenario: Adapter back-compat
    Given a test adapter without send_auto_fix_regression
    When notify_auto_fix_regression dispatches to it
    Then the default no-op inherited from NotificationAdapter runs
     And no exception is raised
```

## Capabilities delivered

- **CAP-T185: `notify_auto_fix_regression` dispatches through
  `send_auto_fix_regression`** (type: technical, status: active)
  *Pre-BC-090 the real path called `send_regression` which used
  `format_regression`. BC-090 adds the dedicated adapter method
  + formatter key, closing the CAP-T178 gap that BC-086
  intended.*
- **CAP-T186: `BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED` env**
  (type: technical, status: active)
  *Default `1`. `0` → audit-only (status flips, Telegram silent).
  Separate from `BEACONRY_NOTIFIER_<NAME>_ENABLED` (per-adapter)
  and `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE` (per-notification-kind
  for BC-089). Keeps the per-kind opt-out pattern consistent.*

Supports: CAP-T178 (distinct auto-fix regression body — was
intention; this makes it reality), CAP-B023.

## Links

- `docs/LIFECYCLE.md § 15.1` (BC-088 — surfaced this bug)
- `docs/plan/BC-086-auto-fix-counter-plan.md` (BC-086 original
  intention)
