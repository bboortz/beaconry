---
id: BC-029
title: Append-only audit log of operator-visible state changes
status: shipped
created: 2026-10-31
branch: feat/BC-029-audit-log
plan: docs/plan/BC-029-audit-log-plan.md
---

# BC-029: Append-only audit log

## Narrative

Operator wakes up to a Telegram notification: "✅ Fixed and verified — CVE-2023-44487". Later: "what if that fix broke a service I don't notice until Monday?" Operator wants forensic trail — *what did the system actually do while I slept*. Pre-BC-029, nothing. Only state the DB; status transitions are lost once a row is updated. BC-029 adds append-only JSONL log that every state-affecting hub operation writes to.

## Expected outcome

`${DATA_DIR}/audit.log` grows one JSON line per state change:

```jsonl
{"ts": 1793427500, "event": "finding_ingested", "finding_id": "...", "cve_id": "CVE-2023-44487", "host": "homelab-dev", "source_ref": "nginx:1.24", "tier": 0, "kev": true, "cvss": 7.5}
{"ts": 1793427890, "event": "status_change", "finding_id": "...", "status": "fixed"}
{"ts": 1793427893, "event": "status_change", "finding_id": "...", "status": "fixed_verified"}
```

Operator: `grep CVE-2023-44487 audit.log` → full timeline. `tail -f` during incident.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Append-only audit log

  Scenario: Status transition appends one event
    Given a finding with id "abc" exists in the DB
    When hub.set_status("abc", "fixed") runs
    Then /data/audit.log gains one line: {"event":"status_change","finding_id":"abc","status":"fixed","ts":N}

  Scenario: New finding appends one event with KEV/tier fields
    Given scout posts an SBOM producing a new KEV finding
    When hub.save_finding(f) runs for a non-duplicate row
    Then /data/audit.log gains one line with event="finding_ingested" + tier, kev, cvss, host, source_ref, cve_id

  Scenario: Append-only invariant — nothing truncates the log
    Given 3 successive status changes
    Then audit.log has exactly 3 lines in order

  Scenario: Fire-and-forget — IO failure is non-fatal
    Given DATA_DIR points at an unwritable path
    When audit.append runs
    Then no exception is raised; a WARNING is logged
    And scans continue uninterrupted

  Scenario: read_events returns newest-first with limit
    Given 10 events written in order a0..a9
    When audit.read_events(limit=3) is called
    Then it returns [a9, a8, a7] (newest-first)
```

## Capabilities delivered

- **CAP-T067: Append-only audit log of operator-visible state changes** (technical, active)

## Links

- IDEAS entry 2026-10-31 "priority: soon".
- Related: BC-020 (findings written), BC-011 (status transitions on probe), BC-009 (fix flow transitions).
