---
id: BC-031
title: Scan reconciliation + regression detection — fix is not forget
status: ready
created: 2026-10-31
---

# BC-031: Scan reconciliation + regression detection

## Narrative

Operator (me) tapped Fix in Telegram on `CVE-X` three months ago. The DB says `status=fixed`. But three weeks later, a dependency upgrade silently rolled it back; six weeks later, a Dockerfile edit reintroduced the vulnerable library; today, that CVE is being actively exploited. Beaconry sees it in every scan but the DB row already says "fixed" — `INSERT OR IGNORE` leaves it alone, operator never gets re-notified. The whole attack window reopens silently.

Beaconry's Fix button is not fire-and-forget. The system must own regression detection forever after a Fix action. Every scout cycle reconciles current scan against the DB; a previously-fixed finding reappearing is a louder alarm than the original.

Operator feedback 2026-10-31 (verbatim):
> "I would prefer to have a rescan for everything we pressed fix it to ensure the fix was really applied and it worked as expected without any regression. Plus it might become introduced by other factors. So ensure the fix works also long term. A fix it button should not be a 'forget it'."

## Trigger

Every scout scan cycle (~300s default). After `process_sbom_entry` yields the fresh finding set, hub reconciles against DB open+fixed rows.

## Expected outcome

Four operator-visible changes:

1. **Auto-close on manual fixes** — AVG-2909 that operator fixed via `sudo pacman -Syu php` auto-transitions `open → fixed_verified` on next scout cycle. No "I ran it" button needed.
2. **Loud regression alarm** — a `fixed` finding that returns to scan transitions `fixed → regressed` with a 🔥 Telegram notification distinct from the first-fire. Suggested body:
   ```
   🔥 FIX REGRESSED — AVG-2909
   Previously fixed on <date>. Reappeared on <host>:<source_ref>.
   Something reinstalled / rolled back the vulnerable version.
   [Investigate]  [Re-fix]  [Accept risk]
   ```
3. **`make findings` surfaces regression status visually** — new `regressed` state with its own color/marker.
4. **Audit log captures every transition** — three new event types: `auto_closed`, `regressed`, `fix_held_through_scan`.

## Out of scope

- Not changing the scan interval. Operators tune via `SCAN_INTERVAL`.
- Not changing BC-011's probe (operator-triggered health probe stays as-is).
- Not building a "fix history" UI (just the current status + audit log — operator can grep the log).
- Not touching `snoozed`/`accepted` statuses — those are explicit operator choices and should be respected.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scan reconciliation keeps fixes verified over time

  Scenario: Manual fix auto-verifies on next scan
    Given finding X has status "open" and is in the current scan
    When operator runs the manual fix command externally
    And scout rescans and finding X is NOT in the new scan
    Then hub transitions X to status "fixed_verified"
    And an audit event "auto_closed" is logged
    And no Telegram notification fires (silent happy path)

  Scenario: Regression triggers a loud alarm
    Given finding X has status "fixed" (operator ran the fix 3 months ago)
    When a fresh scout scan matches X again
    Then hub transitions X to "regressed"
    And an audit event "regressed" is logged with ts_last_fixed + ts_regressed
    And Telegram receives a distinct 🔥 "FIX REGRESSED" notification
    And the message includes both prior-fix date and current observation

  Scenario: Fix holds — audit records the heartbeat (optional)
    Given finding X has status "fixed_verified"
    When a fresh scan does NOT contain X
    Then an audit event "fix_held_through_scan" MAY be logged (debouncing considered)
    And no Telegram fires

  Scenario: Snoozed finding ignored by reconciliation
    Given finding Y has status "snoozed"
    When fresh scans contain or exclude Y
    Then status stays "snoozed" (operator explicitly deferred)

  Scenario: Accepted finding ignored by reconciliation
    Given finding Z has status "accepted"
    When fresh scans contain Z
    Then status stays "accepted" (operator decided to live with it)

  Scenario: regressed → fix_held → back to fixed_verified
    Given finding X is in status "regressed"
    When operator re-applies the fix
    And next scan does NOT contain X
    Then hub transitions X to "fixed_verified"
```

## Capabilities delivered

- **CAP-B021: Operator sees regression alerts — a previously-fixed CVE that returns to scan raises a loud alarm, not silently reopens the attack window**
- **CAP-T071: Scan reconciliation diffs current scan against DB open+fixed rows; transitions status accordingly**
- **CAP-T072: `regressed` DB status with distinct Telegram notification format + make findings column**
- **CAP-T073: Audit log carries `auto_closed`, `regressed`, `fix_held_through_scan` event types**

Depends on / extends:
- BC-009 (fix execution via Telegram) — supersedes its "set status=fixed and done" semantics
- BC-011 (probe verification) — operator-triggered probe stays available as pre-fix safety check
- BC-029 (audit log) — new event types land there

## Scope

- `poc/hub/hub.py` — new `reconcile_scan(host, current)` function; called from `receive_scan` after `process_sbom_entry`
- DB schema: new column `last_fixed_ts INTEGER` (set when transitioning to `fixed`); new status value `regressed`
- `poc/scripts/findings.py` — add column for regressed state + sort key promotion
- `format_notification` branch for `regressed` — distinct 🔥 header
- `poc/hub/audit.py` — no code change; new event types are just strings
- Tests: ~15 unit + 2-3 integration scenarios

## Estimated effort

~1 day. Core logic is small (a dict-diff), but the state machine has 7 transitions to cover and the regression notification needs a thoughtful format.

## Supersedes

Earlier "I ran it" button IDEA — trust-only resolution is explicitly rejected by this story's operator principle. See `~/.claude/.../fix_is_not_forget.md`.

## Links

- Design: `memory/design-decisions.md § Urgency tiers` (regression deserves its own urgency escalation)
- Related: BC-030 (internet_facing — orthogonal; both ready for a dedicated session)
- Memory: [[fix-is-not-forget]]
