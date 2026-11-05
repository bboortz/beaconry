---
id: BC-093
title: Daily rescan + docker events listener + pacman hook template
status: in-progress
created: 2026-11-05
branch: feat/BC-093-rescan-hooks
plan: docs/plan/BC-093-rescan-hooks-plan.md
---

# BC-093: Rescan triggers beyond SCAN_INTERVAL

## Narrative

Operator installs a pip package at 10:00; scout's next SCAN_INTERVAL
(default 300s) catches it. Fine — 5 min delay is tolerable. But the
operator ALSO:
- pulls a new `nginx:1.27` image at 09:30 and starts a container;
  wants scout to notice before the next tick.
- installs `openssl-3.1.4-2-x86_64.pkg.tar.zst` via pacman at 23:50;
  expects scout to catch it immediately so the KEV alert fires
  overnight instead of 5 min after the next 00:00 boot.

Design-decisions.md `§ Inventory cadence` covers both ("real-time
push: pacman post-transaction hook + docker events listener → scout
queues delta") and a daily full rescan at 02:00 host-local to catch
drift. Pre-BC-093 none of these exist.

## Trigger

Three independent triggers:
1. **Scheduled daily tick** at `BEACONRY_DAILY_RESCAN_HOUR:00` host-
   local time.
2. **Docker event** matching image pull/create/start.
3. **Pacman post-transaction** on an Arch host (operator installs
   the shipped hook file).

## Expected outcome

- Daily rescan runs at the configured hour (default 02:00 local).
  Env `-1` disables.
- Docker events listener debounces rapid-fire events (e.g. compose
  up pulling 5 images) via `BEACONRY_DOCKER_EVENTS_DEBOUNCE_S`
  (default 10s), triggers a single scan per burst.
- `poc/hooks/beaconry-pacman.hook` template shipped with
  installation docs; the hook POSTs to scout's `/scan/trigger`.
- All three triggers audit their reason (`scheduled_daily`,
  `docker_event`, `pacman_hook`) via `scan_received` payload
  carrying the trigger-source header.

## Out of scope

- Non-Arch package-manager hooks (apt, dnf, apk). Template-only
  for pacman in v1; other package managers can follow the same
  pattern in future BCs.
- Full inventory diff — scout runs a full scan on every trigger.
  A real "queue a delta" optimisation is deferred.
- Windows / macOS docker events — code only runs on Linux.

## Acceptance criteria (Gherkin)

```gherkin
Feature: rescan triggers beyond SCAN_INTERVAL

  Scenario: Daily rescan fires at configured hour
    Given BEACONRY_DAILY_RESCAN_HOUR=2
      And scout boots at 01:59 local
    When the clock crosses to 02:00 local
    Then _run_scan_once fires with reason=scheduled_daily

  Scenario: Daily rescan disabled with -1
    Given BEACONRY_DAILY_RESCAN_HOUR=-1
    When scout boots
    Then the daily loop does NOT start (log confirms)

  Scenario: Docker events listener triggers scan
    Given BEACONRY_DOCKER_EVENTS_ENABLED=1
      And scout is subscribed to the docker events stream
    When a `image pull` event arrives
    Then _run_scan_once fires with reason=docker_event

  Scenario: Docker events listener debounces burst
    Given BEACONRY_DOCKER_EVENTS_DEBOUNCE_S=10
    When 3 image pulls arrive within 2s
    Then exactly ONE scan is scheduled (not three)

  Scenario: Docker events listener disabled
    Given BEACONRY_DOCKER_EVENTS_ENABLED=0
    When scout boots
    Then no events stream is opened

  Scenario: Pacman hook POSTs to /scan/trigger
    Given operator installs poc/hooks/beaconry-pacman.hook
    When pacman commits a transaction
    Then the hook executes POST http://localhost:8001/scan/trigger
      And scout runs _run_scan_once
```

## Capabilities delivered

- **CAP-T191: Daily full rescan at configured hour**
  (type: technical, status: active)
  *`BEACONRY_DAILY_RESCAN_HOUR` (0-23, default 2; -1 disables)
  schedules a wall-clock-based rescan. Catches slow drift.*
- **CAP-T192: Docker events listener triggers scan on image
  pull/create/start** (type: technical, status: active)
  *Scout subscribes to `docker events`; filters for
  pull/create/start on image or container; debounces per
  `BEACONRY_DOCKER_EVENTS_DEBOUNCE_S`.*
- **CAP-T193: Pacman post-transaction hook template**
  (type: technical, status: active)
  *`poc/hooks/beaconry-pacman.hook` — operator installs manually
  on Arch hosts. POSTs to scout's `/scan/trigger`.*

## Links

- `memory/design-decisions.md § Inventory cadence` (hybrid model
  description)
- `docs/LIFECYCLE.md § 16.3` + `§ 16.4` (both get closed)
