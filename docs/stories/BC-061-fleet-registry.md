---
id: BC-061
title: Scout fleet registry — every scout visible in FLEET with liveness indicator
status: shipped
created: 2026-11-02
branch: feat/BC-061-fleet-registry
plan: docs/plan/BC-061-fleet-registry-plan.md
---

# BC-061: Fleet registry with liveness

## Narrative

Another agent flagged that `hetzner-staging` doesn't appear in FLEET.
Root cause: `/fleet` derives from the findings table (`GROUP BY host`),
so a scout that just posted its first scan and found zero CVEs matching
`MIN_CVSS` is invisible. Likewise a scout that stopped posting leaves
a stale `LAST SEEN` with no indication the connection is broken.

Operator needs:
1. Every scout that has ever posted appears in FLEET.
2. Each row shows whether the agent is still delivering data (STATUS
   column: `active` / `stale` / `dead`).

Add a `scouts` registry table. Upsert on every `/scan` POST — even an
empty-SBOM scan registers the scout. `/fleet` LEFT-JOINs scouts with
findings aggregates so zero-finding scouts are visible.

## Trigger

Agent message: *"Also hetzner-staging doesn't appear in FLEET at all —
let me check why."*

## Expected outcome

- First `POST /scan` from a new host registers it in the `scouts`
  table (host, first_seen_ts, last_posted_ts, last_sbom_count).
- Every subsequent POST bumps `last_posted_ts` + `last_sbom_count`.
- `GET /fleet` returns `[{host, last_seen_ts, last_posted_ts,
  last_sbom_count, open_findings, total_findings, status}, …]`.
  `status` ∈ `{"active", "stale", "dead"}` computed from
  `now - last_posted_ts`:
  - `active` — within 2× SCAN_INTERVAL (default: 600s)
  - `stale`  — within `BEACONRY_FLEET_DEAD_AFTER_S` (default: 86400s)
    but past the active threshold
  - `dead`   — beyond the dead threshold
- `make status` renders a new STATUS column with glyphs:
  `● active` / `⚠ stale` / `✗ dead`.
- Scouts without findings get `open_findings=0`, `total_findings=0`,
  but still appear in FLEET.
- `init_db` backfills the scouts table from existing findings
  (`SELECT host, MIN(found_at), MAX(last_seen_ts)` grouped by host)
  so operators who already have findings don't start with an empty
  registry.

## Out of scope

- Scout de-registration (`make scout-forget HOST=...`). If a scout is
  decommissioned, operator can `DELETE FROM scouts` manually. Follow-up BC.
- Heartbeat posts from scout when idle (scout today only posts when it
  has a scan to send; `active` → `stale` transition happens naturally
  at 2× SCAN_INTERVAL).
- Scout-side change. The POST already carries `host` and
  `scan_received` audit event records sbom_count.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Fleet registry

  Scenario: First scan from new host registers scout
    Given no row in scouts table for `hetzner-staging`
    When POST /scan arrives with host=hetzner-staging and 0 SBOMs
    Then scouts has one row for hetzner-staging with first_seen_ts=now,
      last_posted_ts=now, last_sbom_count=0

  Scenario: Repeated POST bumps last_posted_ts
    Given scouts has a row for `laptop` with last_posted_ts=T0
    When POST /scan arrives with host=laptop at T1 > T0
    Then that row's last_posted_ts == T1

  Scenario: /fleet shows scout with zero findings
    Given scouts has `hetzner-staging` registered with no findings
    When GET /fleet arrives
    Then the response includes a row for hetzner-staging with
      open_findings=0, total_findings=0, status="active"

  Scenario: /fleet status thresholds
    Given scouts: laptop(now-30s), server2(now-5h), oldlaptop(now-5d)
    When GET /fleet arrives
    Then laptop.status == "active"
    And server2.status == "stale"
    And oldlaptop.status == "dead"

  Scenario: init_db backfills scouts from existing findings
    Given findings table has rows for bb-82jq and server2 with
      found_at values set
    And scouts table is empty
    When init_db runs
    Then scouts table has one row per distinct host from findings,
      with first_seen_ts = min(found_at) and last_posted_ts =
      max(last_seen_ts) per host

  Scenario: make status renders STATUS column with glyphs
    Given /fleet returns laptop(active), server2(stale), old(dead)
    When operator runs make status
    Then FLEET section columns include STATUS
    And laptop row shows "● active"
    And server2 row shows "⚠ stale"
    And old row shows "✗ dead"
```

## Capabilities delivered

- **CAP-T121: `scouts` registry table + /scan upsert** (technical).
- **CAP-T122: /fleet LEFT-JOIN with liveness status** (technical).
- **CAP-T123: `make status` renders FLEET STATUS column** (technical).
- **CAP-B044: Operator can see every scout + whether it's delivering
  up-to-date data** (business).

## Links

- Follows up: BC-053-equivalent (/fleet derived from findings — now
  insufficient).
- Depends on: BC-055 (fleet endpoint exists), BC-057 (FLEET section in
  `make status`).
