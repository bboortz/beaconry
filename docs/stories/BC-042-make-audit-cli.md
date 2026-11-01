---
id: BC-042
title: `make audit` CLI — operator troubleshooting view of the audit log
status: shipped
created: 2026-11-01
branch: feat/BC-042-make-audit-cli
plan: docs/plan/BC-042-make-audit-cli-plan.md
---

# BC-042: `make audit` CLI — operator troubleshooting view

## Narrative

Hub writes 7 event types to `/data/audit.log` since BC-029 (`finding_ingested`,
`status_change`, `auto_closed`, `regressed`, `heartbeat_reminder_sent`,
`digest_sent`, `digest_throttled`). The log is the authoritative record of
what beaconry *did*, but operators can only reach it via
`docker exec beaconry-hub cat /data/audit.log` + eye-grep JSON. Every
troubleshooting conversation this session needed audit data:

- *"Why didn't my Telegram notification fire?"* → `finding_ingested` present +
  hub log warnings `notify failed for X: No module named 'poc'` (BC-035/BC-030 session).
- *"When did I snooze CVE-X?"* → `status_change` with `status=snoozed`. Audit
  stores `finding_id`, not `cve_id` — operator had to cross-reference the DB.
- *"How many digests got sent today? How many throttled?"* → `digest_sent` /
  `digest_throttled` counts over a time window.
- *"Did reconcile auto-close anything after my fix?"* → `auto_closed` events.
- *"Did scout even post this cycle?"* → scan reception not currently audited
  (gap — add `scan_received` event in V1).

`make audit` turns these into one grep-free operator-grade CLI.

## Trigger

Operator wants to know what beaconry did in the last window, or when a
specific transition happened, or whether an expected event fired at all.

## Expected outcome

- `make audit` prints the last 50 events as an aligned timeline, resolving
  `finding_id` → `cve_id`+`host` from the DB so operators don't need to join.
- Filters work composably: `SINCE=1h`, `EVENT=regressed,auto_closed`,
  `CVE=CVE-2026-X`, `HOST=homelab`, `FINDING=abc123`, `LAST=200`.
- `COUNT=1` returns a one-line-per-event-type summary over the time window.
- `FOLLOW=1` tails the audit stream (like `tail -f`).
- Compose-aware docker-exec routing like `make findings` / `make status`.
- Help text names every event type the lint knows about + a one-line meaning.

Troubleshooting scenarios (from this session's debugging):

1. **"Did my manual snooze land?"** → `make audit EVENT=status_change CVE=CVE-X`
2. **"Why did I get 50 digests today?"** → `make audit EVENT=digest_sent SINCE=24h`
3. **"Did reconcile close anything after I pulled nginx?"** → `make audit EVENT=auto_closed SINCE=10m`
4. **"Did the heartbeat sweep fire today?"** → `make audit EVENT=heartbeat_reminder_sent SINCE=24h`
5. **"Is scout actually posting?"** → `make audit EVENT=scan_received SINCE=10m` (NEW event in V1).

## Out of scope

- Writing to the audit log from the CLI (append-only semantic preserved).
- Rotating / pruning the audit log (operator ops concern — out of V1).
- Pulling audit events from a remote hub (local docker exec only).
- Rich formatting / colour output beyond the aligned table.

## Acceptance criteria (Gherkin)

```gherkin
Feature: `make audit` CLI

  Scenario: Default run returns the last 50 events as a timeline
    Given audit.log has 120 events
    When operator runs `make audit`
    Then exactly 50 events are shown, newest-first
    And each line has: time, event, cve_id (if any), host, extra fields

  Scenario: SINCE filter accepts human time
    Given audit.log has events spanning 48h
    When operator runs `make audit SINCE=1h`
    Then only events from the last hour are shown

  Scenario: Multi-event filter via comma
    Given audit.log has `digest_sent`, `digest_throttled`, `finding_ingested`
    When operator runs `make audit EVENT=digest_sent,digest_throttled`
    Then only digest events are shown

  Scenario: CVE filter resolves cross-reference
    Given a `status_change` event has `finding_id=abc` and the DB maps `abc → CVE-2026-X`
    When operator runs `make audit CVE=CVE-2026-X`
    Then the status_change event is shown even though its own record has no `cve_id`

  Scenario: COUNT mode summarises by event type
    Given audit.log has 100 events across 5 types
    When operator runs `make audit COUNT=1 SINCE=24h`
    Then output is one line per event type with count + first-last timestamp

  Scenario: `scan_received` event is new in V1
    Given scout POSTs to `/scan` with any payload
    When the hub finishes processing
    Then audit.log has a `scan_received` entry with host, sbom_count, candidate_count

  Scenario: Scout-with-zero-findings is now visible
    Given scout posts with 0 candidate findings
    When operator runs `make audit EVENT=scan_received`
    Then the empty scan still shows up in the timeline
```

## Capabilities delivered

- **CAP-T089: `make audit` CLI with filters + compose-aware routing** (technical, active).
- **CAP-T090: Hub emits `scan_received` audit event per `/scan` POST** (technical, active) — closes the "did scout even post?" blind spot.
- **CAP-B026: Operator can answer every post-hoc troubleshooting question without parsing JSON** (business, active).

Extends: CAP-T049 (BC-029 audit log), CAP-T046 (BC-007 findings CLI pattern), CAP-T052 (BC-013 status CLI pattern).

## Links

- Design: `memory/code-quality.md § shift-left patterns`
- Related: BC-007 (findings CLI; pattern reused), BC-013 (status CLI; compose-aware pattern), BC-029 (audit log source).
