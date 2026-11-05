---
id: BC-086
title: Auto-fix status with counter threshold — snoozed/accepted/open all eligible
status: shipped
created: 2026-11-05
branch: feat/BC-086-auto-fix-counter
plan: docs/plan/BC-086-auto-fix-counter-plan.md
---

# BC-086: Auto-fix status with counter threshold

Operator observation: a finding for `python:3.11-slim-bookworm · libc-bin`
on hetzner-staging stayed in `snoozed` status even after the image was
removed and a fresh scan ran. Audit of `hub.py:661` found that BC-031's
reconcile SQL filter explicitly excludes `snoozed` and `accepted`
statuses — those findings are never auto-closed regardless of what
scout reports. The gap is real and non-obvious: operator thinks snooze
is "defer the decision", but under the hood it's "lose automatic
mitigation detection forever".

## Operator scenario

**Who**: me (bboortz) operating a 3-host fleet. I [Accept risk] or
[Snooze] no-fix findings often; I fix some findings out-of-band via
`docker pull` or image rebuild without pressing [Fix now] in Telegram.

**When**: next scout cycle after an out-of-band mitigation (image
removed, image upgraded, container decommissioned).

**Success**:
- Open / snoozed / accepted findings all get the same treatment: if
  scout stops seeing them for N consecutive scans, hub auto-closes to
  a distinct `auto_fixed` status.
- "N" is operator-configurable via `BEACONRY_AUTO_FIX_THRESHOLD`
  (default 3 — conservative; one scan glitch doesn't trigger).
- `auto_fixed` is visually + semantically distinct from `fixed_verified`
  (which is "operator pressed Fix now + re-scan confirmed clean"). The
  operator can always see WHICH closes were automatic vs
  operator-triggered.
- If an auto-fixed finding reappears, the regression alert explicitly
  says "regressed from AUTO-FIX", not just the generic regression text.
- Every notification carries a "Last seen: X ago" line so the operator
  can tell if a Critical alert is fresh or long-standing.

**Urgency**: Tier 2 — bug-class fix. Not a security CVE, but an
operator-trust gap. Also unblocks the observed noise pattern
(no-fix findings piling up as snoozed with no way to auto-clear).

## Gherkin

```
Feature: counter-threshold auto-fix

Scenario: Open finding auto-closes after N consecutive missing scans
  Given BEACONRY_AUTO_FIX_THRESHOLD=3
   And finding X has status='open' on host H
  When scout runs 3 consecutive scans where X is absent
  Then finding X's status becomes 'auto_fixed'
   And audit event 'finding_auto_fixed' records the prior status + counter value
   And no Telegram notification fires (silent close)

Scenario: Snoozed finding auto-closes after N consecutive missing scans
  Given finding Y has status='snoozed'
  When scout runs threshold-many scans where Y is absent
  Then finding Y's status becomes 'auto_fixed'
   And the prior 'snoozed' status is recorded in the audit event
   And `make findings STATUS=auto_fixed` lists Y

Scenario: Accepted finding auto-closes after N consecutive missing scans
  Given finding Z has status='accepted'
  When scout runs threshold-many scans where Z is absent
  Then finding Z's status becomes 'auto_fixed'
   And the prior 'accepted' status is recorded in the audit event

Scenario: Counter does not increment below threshold
  Given BEACONRY_AUTO_FIX_THRESHOLD=3
   And finding X has status='open' absent_count=1
  When one scan runs where X is still absent
  Then absent_count = 2
   And status stays 'open' (no change)

Scenario: Counter resets if finding reappears
  Given finding X has status='open' absent_count=2
  When a scan runs where X IS present again
  Then absent_count = 0
   And status stays 'open'

Scenario: Auto-fixed regression has a distinct alert
  Given finding X has status='auto_fixed' with auto_fixed_ts=T
  When a later scan sees X present again
  Then status becomes 'regressed'
   And a Telegram alert fires with the subject "🔥 AUTO-FIX REGRESSED"
     (NOT the generic "🔥 FIX REGRESSED" message used for operator-fix regressions)
   And the message body says "Auto-closed at <T> after missing for <N> scans — now back"
   And audit event 'finding_regressed_from_auto_fix' is recorded

Scenario: Threshold is operator-configurable
  Given the operator sets BEACONRY_AUTO_FIX_THRESHOLD=5
  When hub starts
  Then the reconcile loop requires 5 consecutive absent scans before auto-fix
   And `make status` surfaces the configured value in /healthz

Scenario: Telegram notifications include "Last seen"
  Given a finding has last_seen_ts = 4 hours ago
  When a new alert, reminder, or regression message is formatted
  Then the message body contains a "Last seen: 4h ago" line
   And for brand-new findings (first-ever notification), the line says "Last seen: just now"

Scenario: Auto-fixed findings are always discoverable
  Given finding X was auto-fixed yesterday
  When operator runs `make findings`
  Then X appears with status 'auto_fixed' (not hidden)
  When operator runs `make audit EVENT=finding_auto_fixed`
  Then the event history shows X's close event + prior status + counter value
```

## Capabilities

- **CAP-T176** (new): Counter-based auto-fix — new `auto_fixed` status,
  distinct from `fixed_verified` (operator-initiated). Transitions
  from any of `open | regressed | snoozed | accepted` once absent_count
  reaches `BEACONRY_AUTO_FIX_THRESHOLD`.
- **CAP-T177** (new): `BEACONRY_AUTO_FIX_THRESHOLD` configurable
  (default 3). Documented in VARIABLES.md; surfaced in hub `/healthz`
  for operator visibility.
- **CAP-T178** (new): Auto-fix regression — distinct
  `🔥 AUTO-FIX REGRESSED` Telegram message + `finding_regressed_from_auto_fix`
  audit event. Operator can distinguish from manual-fix regressions.
- **CAP-T179** (new): `Last seen: X ago` line on every notification
  (new alert, reminder, regression). `last_seen_ts` column on findings;
  updated on every scan where present.
- Extends **CAP-T031-ish** (BC-031 reconcile): widens the SQL filter
  to include `snoozed + accepted`, adds counter logic, preserves
  existing regression-detection for `fixed_verified → regressed`.

## Out of scope

- Configurable counter *per finding*. Threshold is global for v1.
- `[Un-fix]` Telegram button to flip an `auto_fixed` back to `open`.
  (Operator can already `make set-status` if they disagree.)
- Audit-log-based "N findings auto-fixed in last 24h" daily digest.
  Future BC if the auto-fix rate is high enough to want a summary.
- Auto-fix for `fixed`/`fixed_verified` statuses (those represent
  operator decisions; don't touch).
