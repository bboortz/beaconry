---
id: BC-102
title: Hub-side scout-offline alerts + stale-scout findings reconcile
status: in-progress
created: 2026-11-08
branch: feat/BC-102-scout-offline-alerts
plan: docs/plan/BC-102-scout-offline-alerts-plan.md
---

# BC-102: When a scout goes dark, beaconry notices + acts

## Narrative

2026-11-08 ~21:20 UTC bb-82jq: `beaconry-scout.service` (systemd)
exited cleanly (victim of the container-name collision BC-101
retired). Operator had no visibility: no Telegram, no email, no
log alert. The scout stayed dark for 54 minutes. During that
window, 17 open + 7 regressed findings froze on the hetzner-side
hub — BC-086 reconcile needs scout POSTs to fire, and there were
none. Operator-observable symptoms:

- `make status` → bb-82jq row `⚠ stale`, `last_heartbeat 54m ago`
- `make findings` → still 17 open + 7 regressed, unchanged
- No Telegram
- No audit event

The hub **knew** scout was stale (BC-064 computes `heartbeat_stale`
and surfaces it in `/fleet`). The gap is behavioral: knowing
without acting. Three missing capabilities, all stemming from the
same assumption — "scout POSTs drive everything":

1. **No Telegram alert** when a scout crosses the offline threshold.
2. **No audit trail** for scout up/down transitions.
3. **No reconcile** for a stale scout — findings freeze until the
   scout POSTs again.

BC-102 closes all three with a single background task on hub, plus
three audit events and one notification class.

## Trigger

- 2026-11-08 ~21:20 UTC bb-82jq incident (54-min dark window).
- Also fires preventatively: every future "scout crashed / was
  killed / host rebooted / network partition" situation.
- Confirmed design gap documented in the code at `hub.py:3028` —
  comment says "those will auto-close via BC-031 reconcile" but
  reconcile never fires for a silent scout.

## Expected outcome

- **Telegram alert on offline transition.** One message per offline
  event: *"⚠️ Scout on `bb-82jq` offline since 21:20:09 CET. Expected
  heartbeat every 60s; last 15m ago. Findings on this host will
  auto-close after 3 missed scan cycles (~15m). Check `systemctl
  status beaconry-scout.service` on the host."* Edge-triggered: one
  alert per transition from online → offline, not continuous.
- **Telegram alert on recovery.** One message when a previously-
  offline scout POSTs again: *"✓ Scout on `bb-82jq` back online.
  Was offline for 1h 7m. Resumed normal scanning."*
- **Audit events** emitted for both transitions:
  - `scout_offline` — fields: `host`, `last_heartbeat_ts`,
    `offline_duration_s`, `last_health_label`.
  - `scout_online` — fields: `host`, `was_offline_duration_s`,
    `new_last_posted_ts`.
  - `finding_auto_fixed_stale_scout` — one per finding closed by
    the stale-scout reconciler (distinct from BC-086's
    `finding_auto_fixed` so operators can filter).
- **Stale-scout reconcile.** On a fixed schedule (every heartbeat
  interval while a scout is offline), bump `absent_count` on all of
  the scout's `open`/`regressed`/`snoozed`/`accepted` findings. On
  the third bump, flip to `auto_fixed` with `auto_fixed_ts=now` and
  emit `finding_auto_fixed_stale_scout` audit. Same `AUTO_FIX_THRESHOLD`
  behavior BC-086 has for POST-driven reconcile — one unified
  semantic, two triggers.
- **Opt-out knob.** `BEACONRY_AUTO_CLOSE_STALE_SCOUT=0` in the hub
  `.env` disables ONLY the stale-scout auto-close (operators who
  want frozen state as "we don't know" signal stay with the
  pre-BC-102 behavior). The Telegram alert + audit events always
  fire — not opt-out.
- **Threshold tunable.** `BEACONRY_SCOUT_OFFLINE_ALERT_THRESHOLD_S`
  (default `600` = 10 min) names the "truly offline" bar.
  Deliberately higher than the FLEET `heartbeat_stale` cutoff
  (`max(2*SCOUT_HEARTBEAT_INTERVAL_S, 300)` = 300s minimum) so a
  normal 2-minute network glitch doesn't page.

## Out of scope

- **Fine-grained container-lifecycle events via scout → hub.** Scout
  already subscribes to docker events locally (BC-093) but uses
  them only to trigger rescans. Forwarding those events to hub for
  a global "container state changed" feed would be a separate BC
  with much broader scope (hub DB schema extension, UI model
  change). BC-102 covers the one event that bit us: scout-itself
  going offline.
- **Per-finding health probe.** Containers and their findings are
  separate concerns.
- **Revoking the frozen-is-safer assumption everywhere.** BC-102
  defaults to auto-close because the incident showed frozen-forever
  is worse. Operators who disagree set the env to `0` per host.
- **Self-healing: hub auto-restarts offline scouts.** Not possible
  — hub has no credentials on the operator host. The Telegram
  alert tells the operator to intervene.
- **Scout-offline sweep running on the dev stack / against a test
  scout.** BC-038's scenario runner mocks scout POSTs directly; the
  sweep loop fires on scheduled intervals and would complicate
  scenarios. Make it opt-in via env (default on in production
  compose; left off in test harnesses).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Hub detects + acts when scout goes offline

  Background:
    Given beaconry-hub with scout "host-a" registered + active
      And BEACONRY_SCOUT_OFFLINE_ALERT_THRESHOLD_S=600 (default)
      And BEACONRY_AUTO_CLOSE_STALE_SCOUT=1 (default)
      And "host-a" has 2 open + 1 regressed finding

  Scenario: Scout stops POSTing — Telegram alert fires once
    Given scout "host-a" has posted within the last 10 min
    When scout "host-a" stops POSTing for 10+ minutes
    Then the hub emits exactly one `scout_offline` audit event for "host-a"
      And the hub sends exactly one Telegram message mentioning "host-a offline"
      And the message includes the last_heartbeat timestamp
      And the message mentions the auto-close timeline

  Scenario: Scout stays offline — no repeat alert
    Given scout "host-a" is offline + Telegram alert already sent
    When another 10 minutes pass with no POST
    Then no additional `scout_offline` audit event fires for "host-a"
      And no additional Telegram message is sent

  Scenario: Stale-scout reconcile auto-closes findings
    Given scout "host-a" has been offline for 10+ minutes
      And the background sweep runs on its interval
    When the sweep observes the stale state 3 consecutive times
    Then each of "host-a"'s 3 findings flips to `auto_fixed`
      And each auto-close emits `finding_auto_fixed_stale_scout` audit
      And the auto-close event records `host-a` and the offline duration

  Scenario: Scout comes back — recovery alert + resumed reconcile
    Given scout "host-a" has been offline for 12 minutes
    When scout "host-a" POSTs a fresh scan
    Then the hub emits `scout_online` audit for "host-a"
      And the hub sends one Telegram "back online" message
      And subsequent POST-driven reconciles resume (BC-086 path)

  Scenario: Opt-out — auto-close disabled
    Given BEACONRY_AUTO_CLOSE_STALE_SCOUT=0
    When scout "host-a" stays offline for 30 minutes
    Then the `scout_offline` alert + audit still fire
      And NO findings auto-close via the stale-scout path
      And findings stay `open` / `regressed` until POST-driven reconcile resumes

  Scenario: Fresh scout registration does NOT trigger an offline alert
    Given no record of "host-b" in the scouts table
    When "host-b" POSTs its first scan
    Then no `scout_offline` alert fires (last_offline_alert_ts stays 0)
      And a normal finding ingest happens
```

## Capabilities delivered

- **CAP-B050: Operator-visible detection of scout outages** (type:
  business, status: active). Closes the "beaconry silently loses
  visibility when a scout dies" class — BC-102 is the direct answer
  to the 2026-11-08 bb-82jq incident.
- **CAP-T204: `scout_offline_sweep` background task on hub** (type:
  technical, status: active). Periodic sweep; edge-triggered
  Telegram; stale-scout reconcile (same AUTO_FIX_THRESHOLD as
  BC-086 but time-based). Wraps the three missing capabilities in
  one task.

Reference existing CAPs:
- Extends **CAP-T148** (BC-086 auto-fix threshold reconcile) —
  same semantics, new trigger.
- Supports **CAP-B023** (operator trust in the beaconry supply chain).
- Depends on CAP-T044 (BC-061 scout fleet registry — the scouts
  table stores last_posted_ts).

## Open questions

1. **Scout crashed vs scout restarting vs host rebooted — same
   Telegram message?** For V1: yes. One message shape. Future:
   separate "host unreachable" (network-level) vs "scout service
   exited" (if hub can tell the difference, which it can't without
   agent on host).
2. **Should the recovery alert include "auto-close summary"?** If
   stale-scout reconcile closed 3 findings during the outage, the
   recovery Telegram could say *"Resumed. 3 findings were auto-
   closed during the outage."* Nice for forensics. Scope creep?
   Candidate for plan doc design decision.
3. **Snoozed/accepted findings** — should the stale-scout path
   respect operator intent (don't auto-close) or treat them like
   open findings (auto-close)? BC-086 auto-closes them because
   "the image is gone, operator's decision is moot". Same semantic
   here.

## Links

- Incident: 2026-11-08 ~21:20 UTC bb-82jq, 54-min dark window
- BC-101 (sibling — retired the container-name collision that
  triggered this incident)
- BC-086 (auto-fix threshold reconcile — same semantic, POST-driven)
- BC-064 (`heartbeat_stale` computation — read by `/fleet` +
  `make status`)
- BC-061 (scouts table, fleet registry)
- `hub.py:3028` — code comment documenting the exact gap this
  story retires
