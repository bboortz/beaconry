---
id: BC-036
title: Decouple digest cadence from scan cadence — BEACONRY_DIGEST_INTERVAL_S
status: shipped
created: 2026-10-31
branch: feat/BC-036-digest-cadence
plan: docs/plan/BC-036-digest-cadence-plan.md
---

# BC-036: Decouple digest cadence from scan cadence

## Narrative

Operator at desk, 2026-10-31: *"How to configure how often I get '📋 53 additional finding(s) suppressed this run' notifications?"* With `SCAN_INTERVAL=300` and `BEACONRY_MAX_FINDINGS_PER_RUN=10` on an inventory with 63 unique CVEs, every scout cycle produces 53 suppressed findings and fires one digest message. The digest cadence is a side-effect of the scan cadence — no dial.

New-finding notifications *should* fire immediately (correctness latency is tier-dependent — 15 min for Tier 0/1). But reminders / digests are summarisations of state that doesn't change scan-to-scan — reposting the same digest 288 times per day is noise, not signal. BC-035 already shipped this split for the heartbeat loop (BEACONRY_HEARTBEAT_INTERVAL_S); BC-036 extends the pattern to the digest.

## Trigger

Hub finishes a scan, `receive_scan` has a non-empty `suppressed` list ready to render.

## Expected outcome

- Digest fires at most once per `BEACONRY_DIGEST_INTERVAL_S` per host.
- If the suppressed set *changes* (new CVE enters, snoozed one drops out), fire sooner — don't make the operator wait on stale state.
- First-fire notifications for new findings keep firing immediately — no interval gating.
- Operator can opt out of throttling with `BEACONRY_DIGEST_INTERVAL_S=0` (restore pre-BC-036 "every scan" behavior).

## Out of scope

- Per-adapter cadence (email digest every 24h, Telegram digest every 1h). The gating lives at hub level, before adapter dispatch; the whole adapter registry sees the throttled signal.
- Multi-host digest aggregation. Each host tracks its own cadence independently.
- Interactive "snooze digest for 24h" button. The env dial is the only knob in V1.
- Reconcile/regression notifications. Those are correctness-latency-sensitive like first-fires; keep firing immediately.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Digest cadence decoupled from scan cadence

  Scenario: First digest in a window fires
    Given no prior digest sent for host "h1"
    And scan produces suppressed findings S1
    When receive_scan runs
    Then one digest is dispatched
    And host "h1" has `last_digest_sent_ts = now` and `last_digest_hash = hash(S1)`

  Scenario: Repeat scan within interval with same suppressed set — no digest
    Given host "h1" has `last_digest_sent_ts = now - 1h`
    And BEACONRY_DIGEST_INTERVAL_S = 86400 (24h)
    And scan produces the same suppressed set S1
    When receive_scan runs
    Then NO digest is dispatched

  Scenario: Suppressed set changes within interval — digest fires
    Given host "h1" has `last_digest_sent_ts = now - 1h`
    And last_digest_hash encodes set S1
    And scan produces set S2 (different ids)
    When receive_scan runs
    Then a digest IS dispatched
    And last_digest_hash is updated to hash(S2)

  Scenario: Interval elapsed — digest fires even with unchanged set
    Given host "h1" has `last_digest_sent_ts = now - 25h`
    And interval = 24h
    When receive_scan produces S1 (same set)
    Then a digest IS dispatched (operator benefits from a periodic "still 53 suppressed" nudge)

  Scenario: Operator opt-out — INTERVAL_S=0 behaves like pre-BC-036
    Given BEACONRY_DIGEST_INTERVAL_S=0
    When any scan produces suppressed findings
    Then a digest IS dispatched every time (no throttling)

  Scenario: Empty suppressed set never produces a digest regardless of interval
    Given scan produces zero suppressed findings
    When receive_scan runs
    Then NO digest is dispatched (preserved BC-022 short-circuit)

  Scenario: Audit log records digest sent AND skipped-due-to-throttle events
    Given a digest is throttled (within interval, unchanged set)
    When receive_scan runs
    Then audit.log has one `digest_throttled` event with `host`, `hash`, `seconds_since_last`
```

## Capabilities delivered

- **CAP-T080: Digest cadence throttle with content-hash fallback** (technical, active) — new `digest_state` table + `should_send_digest(host, suppressed)` policy function + env toggle.

Extends: CAP-B003 (notification surface), CAP-T079 (heartbeat sweep — same cadence-vs-content pattern).

## Links

- Design: `memory/design-decisions.md § Notification format`
- Related: BC-022 (digest introduced), BC-035 (adapter architecture dispatches digest), BC-034 (last_seen_ts — different data plumbing but same "track state across scans" shape).
