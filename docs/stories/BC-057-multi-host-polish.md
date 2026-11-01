---
id: BC-057
title: Multi-host notification lead + make findings HOST= filter
status: shipped
created: 2026-11-01
branch: feat/BC-057-multi-host-polish
plan: docs/plan/BC-057-multi-host-polish-plan.md
---

# BC-057: Multi-host polish

## Narrative

Operator's real environment has findings from two hosts (`bb-82jq` +
`server2` seen in `make status` FLEET). Two small operator-UX gaps:

1. `make findings` can't filter by host — in a mixed-host inventory
   the operator can't zoom in on one scout's rows.
2. Telegram notifications carry `Host: <host>` on line 2 but in a
   multi-host inventory "where is this?" is the most important signal
   and should lead the message.

Port the two features from the pre-reset BC-051 branch under BC-055
short-name conventions.

## Trigger

Operator runs multi-host and wants to focus on one scout OR see at a
glance which host a Telegram alert came from.

## Expected outcome

- `make findings HOST=laptop` filters DB rows to a single scout's
  findings. Composes with `STATUS=`. Zero change for single-host.
- `format_notification` prepends `🖥️ <code>host</code>` as the lead
  line when the DB has findings from ≥2 distinct hosts. Single-host
  inventories see today's format unchanged — hub promotes the prefix
  automatically when a second scout starts posting.

## Out of scope

- Fleet-wide actions (snooze across hosts, bulk fix).
- Scout de-registration.
- Staleness coloring in `make status` FLEET section.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Multi-host polish

  Scenario: make findings HOST=X filters
    Given a DB with findings on hosts laptop + server2
    When operator runs `make findings HOST=server2`
    Then only server2's rows appear

  Scenario: HOST + STATUS compose
    Given a DB with mixed hosts and statuses
    When operator runs `make findings HOST=laptop STATUS=open`
    Then only rows with host=laptop AND status=open appear

  Scenario: Telegram leads with host when multi-host
    Given hub has findings from laptop + server2
    When format_notification is called with a server2 finding
    Then the first line is "🖥️ <code>server2</code>"

  Scenario: Telegram unchanged for single-host inventory
    Given hub has findings only from server2
    When format_notification is called
    Then the first line is the severity line (no host-prefix clutter)
```

## Capabilities delivered

- **CAP-T113: `make findings HOST=` filter** (technical).
- **CAP-T114: Multi-host-aware notification lead line** (technical).
- **CAP-B040: Operator with ≥2 scouts can tell at a glance which host
  a notification came from and filter CLI output by host** (business).

## Links

- Depends on: BC-055 (short-name env + split-token world).
- Related: BC-053-equivalent FLEET section in `make status` already in
  BC-055.
