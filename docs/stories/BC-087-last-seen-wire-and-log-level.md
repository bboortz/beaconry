---
id: BC-087
title: Wire `Last seen: X ago` into notifications + BEACONRY_LOG_LEVEL env
status: shipped
created: 2026-11-05
branch: feat/BC-087-last-seen-wire-and-log-level
plan: docs/plan/BC-087-last-seen-wire-and-log-level-plan.md
---

# BC-087: Wire `Last seen` into notify() + add BEACONRY_LOG_LEVEL

Two follow-ups from BC-086 ship-verification:

1. **Bug**: BC-086 added `format_last_seen()` + the `last_seen_ts`
   kwarg on `format_notification/heartbeat/regression/
   auto_fix_regression`, but **the live `notify*()` call sites never
   pass `last_seen_ts`** — kwarg defaults to None → line omitted.
   Operator caught it immediately on the first real Telegram alert
   after shipping BC-086 (the 4 bb-82jq alerts for
   debian:bookworm-slim + postgres:17 + nginxproxy had no "Last
   seen" line). Format helper + unit tests are solid; the production
   wire-up is the gap.

2. **Feature**: BC-086 ship-verification required INFO-level
   diagnostic logs in `reconcile_scan` + `bump_last_seen` to prove
   natural counter progression. Now proven, but the logs remain on
   by default (~20 lines per scan cycle × 3 hosts). Need a
   configurable log level so the operator can quiet them when not
   debugging. Side benefit: unlocks DEBUG-level verbosity for any
   future diagnostic without another ship cycle.

## Operator / developer scenario

**Who**: operator receiving Telegram alerts; developer tuning hub
verbosity.

**Success**:
- Next Telegram first-fire / heartbeat / regression / auto-fix-
  regression carries a `Last seen: X ago` line in its body.
- Operator can set `BEACONRY_LOG_LEVEL=DEBUG` in `.env`, rebuild,
  and see the BC-086 reconcile per-finding trace. Default INFO
  stays unchanged — current production noise floor is identical.
- No new dependencies; no schema change; no breaking behaviour.

**Urgency**: Tier 2 — bug fix (visible in every alert) + operator-
QoL env var. Both small.

## Gherkin

```
Feature: last-seen wire-up + configurable log level

Scenario: Telegram first-fire carries Last seen line
  Given a new finding is ingested on scan N
   And `notify(finding)` fires
  When format_notification is called
  Then last_seen_ts from the DB row is passed through
   And the rendered body contains a "Last seen: just now" line
     (just now because save_finding seeded last_seen_ts=now on this scan)

Scenario: Telegram regression carries Last seen line
  Given a previously fixed finding reappears
  When notify_regression fires
  Then format_regression receives last_seen_ts
   And the body contains "Last seen: Xh ago" where X was the gap

Scenario: Heartbeat reminder carries Last seen line
  Given an open finding sits > HEARTBEAT_INTERVAL
  When the heartbeat sweep fires notify_heartbeat
  Then last_seen_ts from DB is passed
   And body contains "Last seen: Xh ago"

Scenario: AUTO-FIX regression carries Last seen line
  Given an auto_fixed finding reappears
  When notify_auto_fix_regression fires
  Then last_seen_ts passes through
   And body contains "Last seen: Xd ago" (because auto_fix happened
     after N scans missing; now it's back)

Scenario: BEACONRY_LOG_LEVEL env controls verbosity
  Given BEACONRY_LOG_LEVEL=DEBUG set in .env
  When hub + scout boot
  Then log output includes BC-086 `reconcile fid=...` + `bump_last_seen` lines
   And switching back to BEACONRY_LOG_LEVEL=INFO hides them

Scenario: Default log level preserves current behaviour
  Given BEACONRY_LOG_LEVEL is unset
  Then hub + scout default to INFO
   And BC-086's diag lines (now at log.debug) are NOT emitted
   And existing log.info/log.warning lines still appear
```

## Capabilities

- **CAP-T180** (new): Every finding notification body carries a
  `Last seen: X ago` line. Closes BC-086's wire-up gap.
- **CAP-T181** (new): `BEACONRY_LOG_LEVEL` env var (DEBUG, INFO,
  WARNING, ERROR; default INFO). Applies to hub + scout root
  loggers at process boot.

## Out of scope

- Per-module log level (e.g. BC-086 traces on, grype chatter off).
  Single global level for v1.
- Changing the `Last seen` humanisation semantics (just-now / Nm /
  Nh / Nd). BC-086's helper stays.
- Adding `Last seen` to the digest (digest is a batch summary; one
  "Last seen" per row would clutter the message).
