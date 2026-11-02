---
id: BC-063
title: Fleet rows surface version, health, and last-scan — troubleshoot deployments fast
status: shipped
created: 2026-11-02
branch: feat/BC-063-fleet-health-visibility
plan: docs/plan/BC-063-fleet-health-visibility-plan.md
---

# BC-063: Fleet health visibility

## Narrative

BC-061 made every scout visible in `make status` FLEET. BC-062 added
versioning. But the FLEET rows still only show `HOST / LAST SEEN /
OPEN / TOTAL / STATUS` — not version, not health, not last-scan.

When `server2` goes stale, the operator sees `⚠ stale 1h ago` and
has no way to tell whether:
- scout crashed (process dead),
- scout running old code that broke on a schema change,
- scout running but Docker socket dropped (health=degraded),
- scout running + healthy but network partition to hub.

Reason: troubleshooting deployments. Urgency: operator currently
cannot troubleshoot a stale `server2` after 1h. The homelab key
user has been bitten.

## Trigger

Operator ran `make status` and reported: "every device on fleet should
show version, health, last scan as well. It must become clear if all
devices are healthy. In all cases the health should clear within 5
minutes of a scout start." (2026-11-02).

## Expected outcome

### Fleet rows gain three columns

```
FLEET:
HOST             LAST SEEN  LAST SCAN  VERSION             HEALTH    OPEN  TOTAL  STATUS
hetzner-staging  4m ago     4m ago     0.1.0 · api1        ● healthy  1     10     ● active
bb-82jq          4m ago     4m ago     0.1.0 · api1        ● healthy  5     5      ● active
server2          1h ago     1h ago     0.0.9 · api1 ⚠      ⚠ degraded 0     5      ⚠ stale
```

- **LAST SCAN**: scout's own `last_scan_ts` (not just when it last
  POSTed metadata). For the current loop-every-5-min scouts, this is
  effectively the same, but the distinction matters for pure
  heartbeat posts.
- **VERSION**: `software · apiN` (same shape as the hub row's VERSION
  column). Mismatched API vs hub → trailing `⚠` glyph on the cell.
- **HEALTH**: `● healthy` / `⚠ degraded` / `? unknown`. Unknown =
  pre-BC-063 scout that doesn't post health.

### Scout POSTs its own metadata

Scout's `/scan` POST body gains three top-level fields:

```json
{
  "host": "server2",
  "timestamp": 1762099200,
  "software_version": "0.1.0",
  "api_version": 1,
  "health": "ok",
  "sboms": […],
  …
}
```

`health` is `"ok"` or `"degraded"` — same string the scout `/healthz`
summary already uses. `software_version` + `api_version` are read
directly from the module constants that BC-062 added to scout.py.

### Scouts heartbeat every ~60s (comfortable default)

A full scan with syft can take seconds-to-minutes and runs every
`SCAN_INTERVAL` (default 300s). That's too long between "we know
this scout is alive" events for fast troubleshooting.

Scouts also POST a tiny `/heartbeat` on a short interval
(`SCOUT_HEARTBEAT_INTERVAL_S`, default **60s**). Payload is only:

```json
{
  "host": "server2",
  "timestamp": 1762099240,
  "software_version": "0.1.0",
  "api_version": 1,
  "health": "ok"
}
```

Hub stores it the same way `/scan` stores its metadata — same
scouts-table row, same version/health/API columns — but **does not
bump `last_scan_ts`**. FLEET renders `LAST SEEN` (either POST) and
`LAST SCAN` (actual scan POST) as two separate columns so the
operator can tell "scout alive, scanning slowly" apart from "scout
dead".

Zero-config: default-on at 60s. Set `SCOUT_HEARTBEAT_INTERVAL_S=0` to
disable (falls back to BC-061 behaviour where `/scan` is the only
heartbeat). Scout fires one heartbeat *immediately* at startup so
operators don't wait 60s to see a freshly booted scout.

### Scout fires initial scan on startup

Previously scout waits `SCAN_INTERVAL` (default 300s = 5min) before
the first scan. That means a freshly deployed scout is invisible in
`make status` for up to 5 minutes.

Change: fire the first scan immediately after the HTTP server comes
up (0s wait), then settle into the normal `SCAN_INTERVAL` loop.
Result: within seconds of a scout boot, it appears in FLEET with
version + health + last_scan.

### Schema version bumps 2 → 3

New columns on the `scouts` table:
- `software_version TEXT` (NULL for pre-BC-063 posts)
- `api_version INTEGER` (NULL for pre-BC-063)
- `last_health TEXT` (NULL for pre-BC-063; '-' in display)
- `last_scan_ts INTEGER` (NULL = never scanned; distinct from
  `last_posted_ts` which bumps on every heartbeat too)

Migration path: `ALTER TABLE scouts ADD COLUMN …` run inside `init_db`
if PRAGMA user_version < 3. Each ADD COLUMN is idempotent-ish — guard
with a `PRAGMA table_info(scouts)` check so a re-run doesn't blow up.

## Out of scope

- Hub pulling health from scouts (push model stays; scouts post, hub
  consumes). Would need network discovery.
- Per-column CLI filters (`make status FLEET_HEALTH=degraded`).
- Historical trend chart. Just the live row.
- Alerting on degraded scouts via Telegram. Degraded == "visible" is
  enough for the operator's manual troubleshooting flow.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Fleet health visibility

  Scenario: Scout POST advertises version + health + scan timestamp
    Given a scout with SOFTWARE_VERSION 0.1.0 and healthy Docker socket
    When it POSTs /scan to the hub
    Then the payload carries software_version="0.1.0", api_version=1,
      health="ok", and a last_scan timestamp

  Scenario: Hub persists per-scout metadata on /scan upsert
    Given a scout POSTed version + health + last_scan
    When init_db has brought the DB to schema v3
    Then the scouts table row carries software_version, api_version,
      last_health populated for that host

  Scenario: /fleet exposes per-scout metadata
    Given one scout posting v0.1.0 healthy + one legacy scout
    When GET /fleet is called
    Then the first row has software_version, api_version, last_health
    And the legacy row has those fields set to null

  Scenario: make status renders VERSION + HEALTH + LAST SCAN columns
    Given /fleet returns the enriched rows
    When operator runs make status
    Then each FLEET row shows a VERSION cell, a HEALTH cell with
      ● healthy / ⚠ degraded / ? unknown, and a LAST SCAN cell

  Scenario: Scout API-mismatch glyph in VERSION cell
    Given scout serves api_version=2 but hub serves api_version=1
    When operator runs make status
    Then the scout's VERSION cell ends with ⚠

  Scenario: Scout fires initial scan on startup
    Given a freshly-booted scout
    When the HTTP server is up
    Then the first /scan POST fires immediately (not after SCAN_INTERVAL)

  Scenario: Scout heartbeats every SCOUT_HEARTBEAT_INTERVAL_S
    Given a scout with SCOUT_HEARTBEAT_INTERVAL_S=60
    When 60s elapse without a scan
    Then the scout POSTs /heartbeat with host + version + api_version + health
    And the hub's scouts row shows last_posted_ts updated
    And last_scan_ts is unchanged

  Scenario: Setting SCOUT_HEARTBEAT_INTERVAL_S=0 disables heartbeats
    Given a scout with SCOUT_HEARTBEAT_INTERVAL_S=0
    When the HTTP server is up
    Then no /heartbeat task runs
    And only /scan POSTs reach the hub (legacy BC-061 behaviour)

  Scenario: Legacy scout (pre-BC-063 payload) still works
    Given a scout POSTing /scan without software_version/api_version/health
    When hub ingests it
    Then the row persists with those columns NULL
    And /fleet returns null for those fields
    And make status renders "-" in the version + health cells

  Scenario: Schema v2 → v3 migration is idempotent
    Given a DB at PRAGMA user_version=2 with scouts rows
    When init_db runs
    Then PRAGMA user_version becomes 3
    And existing scouts rows gain the three new columns as NULL
    And running init_db a second time is a no-op
```

## Capabilities delivered

- **CAP-T129: Scout POSTs software_version + api_version + health**
  (technical).
- **CAP-T130: Scouts table schema v3 — per-scout metadata columns**
  (technical).
- **CAP-T131: /fleet surfaces per-scout version + health + last_scan**
  (technical).
- **CAP-T132: `make status` FLEET renders VERSION + HEALTH + LAST SCAN
  columns + API-mismatch glyph** (technical).
- **CAP-T133: Scout fires initial scan immediately on startup**
  (technical).
- **CAP-T134: Scout heartbeat (POST /heartbeat on short interval) +
  hub endpoint + `last_scan_ts` separation** (technical).
- **CAP-B046: Operator can diagnose a stale/degraded scout from `make
  status` alone — no SSH, no `docker logs`** (business).

## Links

- Depends on: BC-061 (scouts table), BC-062 (versioning + schema
  migration pattern).
- Related: BC-013 (scout HTTP endpoints — reused for health summary).
- Potential follow-up: scout → hub push of its /healthz full body
  (checks dict), so operator can see which specific check is failing.
