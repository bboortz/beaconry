---
id: BC-064
title: FLEET column names unambiguous + stale-heartbeat health degradation
status: shipped
created: 2026-11-02
branch: feat/BC-064-fleet-column-clarity
plan: docs/plan/BC-064-fleet-column-clarity-plan.md
---

# BC-064: FLEET column clarity

## Narrative

BC-063 shipped `/heartbeat` + `last_posted_ts` wiring, but the FLEET
column header still reads **LAST SEEN** — meaning "when did the hub
last hear from this scout, by any protocol". With heartbeat enabled
(default 60s), ~every row in the wild has `last_posted_ts` driven by
heartbeat, not by `/scan`. Operator reads LAST SEEN and asks: is
this when the scout last scanned? When it last pinged? Both?

Second issue: the HEALTH cell is a cached snapshot from the last
heartbeat. If the scout stopped heartbeating 1h ago, the row shows
`● healthy · LAST SEEN 1h ago` — but we have no idea if the scout
is still healthy. The cached label lies.

Both bite the same homelab key user on the same `server2` row.

## Trigger

Operator ran `make status` 2026-11-02 and reported: "It is not last
seen, more last scan right? Last healthcheck should be mentioned as
well." On the same output, `server2` showed `LAST SEEN 1h ago · ●
healthy · ● active` — contradictory on its face.

## Expected outcome

### Rename

FLEET column header `LAST SEEN` → `LAST HEARTBEAT`. The semantics
don't change (still `last_posted_ts`); only the label becomes honest
about what it measures.

### Stale-heartbeat → unknown health

The HEALTH cell renders `? unknown` when
`now - last_posted_ts > SCOUT_HEARTBEAT_INTERVAL_S × 2`
(with a floor of 300s when the operator disabled heartbeats). This
threshold is the hub's own, read at render time — operators who
tune the heartbeat interval get the threshold tracking automatically.

Previously `? unknown` fired only when `last_health IS NULL`
(= pre-BC-063 scout that never reported). Post-BC-064 it also fires
on stale heartbeat.

### Repoint the column, don't just relabel

**Discovered mid-ship:** `LAST SEEN` in the FLEET output has been
reading `MAX(findings.last_seen_ts)` since BC-055 — i.e. "when did
any CVE last get observed for this host". NOT `scouts.last_posted_ts`.
Live-stack data on 2026-11-02 made this obvious: `hetzner-staging`
rendered `LAST SEEN 12m ago · LAST SCAN 57s ago` — a scan is a
post, so `last_posted_ts` must be ≥ 57s, not 12m. The two columns
were measuring different things.

**Fix:** FLEET's `LAST HEARTBEAT` column reads
`scouts.last_posted_ts`, which bumps on every POST (scan + heartbeat).
The old `last_seen_ts` field stays in the `/fleet` response for
back-compat with any CLI pinned to the previous shape, but
`status.py` no longer surfaces it.

Still one new field per row: `heartbeat_stale` (bool, computed
server-side against the heartbeat threshold so clients don't need to
know the number).

## Out of scope

- Changing the `status` glyph (active/stale/dead) interaction — the
  BC-061 windows still drive STATUS, independent of HEALTH.
- Alerting on stale heartbeats via Telegram.
- Adding a dedicated LAST HEALTHCHECK column distinct from LAST
  HEARTBEAT: a heartbeat **is** the health check from the scout's
  side, so a second column would duplicate the first.

## Acceptance criteria (Gherkin)

```gherkin
Feature: FLEET column clarity

  Scenario: FLEET header reads LAST HEARTBEAT not LAST SEEN
    Given at least one BC-063+ scout has posted
    When operator runs make status
    Then the FLEET header contains "LAST HEARTBEAT"
    And it does not contain "LAST SEEN"

  Scenario: Stale heartbeat renders '? unknown' health
    Given a scout whose last_posted_ts is older than 2× heartbeat interval
    When operator runs make status
    Then that row's HEALTH cell is "? unknown"
    And the scout's cached last_health value is ignored

  Scenario: Fresh heartbeat renders the reported health
    Given a scout whose last_posted_ts is within the threshold
    When operator runs make status
    Then the HEALTH cell shows the scout's cached last_health

  Scenario: Heartbeat disabled falls back to a safe default threshold
    Given a hub with heartbeat_interval=0
    When /fleet computes heartbeat_stale
    Then the threshold is 300s (safe floor)

  Scenario: LAST HEARTBEAT reads scouts.last_posted_ts (not findings)
    Given a scout with scouts.last_posted_ts=57s ago and
      MAX(findings.last_seen_ts)=12m ago
    When operator runs make status
    Then LAST HEARTBEAT renders "57s ago" (not "12m ago")
```

## Capabilities delivered

- **CAP-T135: /fleet computes heartbeat_stale per row + status.py
  renders LAST HEARTBEAT column** (technical, supersedes CAP-T132's
  label).
- **CAP-T136: HEALTH cell renders '? unknown' on stale heartbeat
  — cached health label can't lie beyond 2× interval** (technical).

## Links

- Depends on: BC-063 (/heartbeat endpoint + schema v3).
- Related: CAP-T132 (make status FLEET columns — label updated here).
