---
id: BC-118
title: `beaconry status` — show probed URLs, running host, fleet (you) marker, and explain false-downs
status: in-progress
created: 2026-11-15
branch: feat/BC-118-status-url-host-fleet-linkage
plan: docs/plan/BC-118-status-url-host-fleet-linkage-plan.md
---

# BC-118: `beaconry status` reveals what each row is actually probing

## Narrative

Operator on `bb-82jq` runs `beaconry status`:

```
NAME            STATE    HEALTH   VERSION                LAST SCAN  DETAILS
beaconry-hub    running  healthy  0.21.0 · api1 · sch10  1m ago     grype_db ok · telegram polling
beaconry-scout  down     -                               -          container not up

FLEET:
HOST             LAST HEARTBEAT  LAST SCAN  VERSION        HEALTH     OPEN  TOTAL  STATUS
bb-82jq          -               5d ago     0.21.0 · api1  ● healthy  6     64     ● active
server2          40s ago         6d ago     0.21.0 · api1  ● healthy  1     5      ● active
hetzner-staging  42s ago         1m ago     0.21.0 · api1  ● healthy  11    37     ● active
```

The operator can't tell:

- **Which URL** `beaconry-hub` probed — is that the local dev-compose
  hub on `http://localhost:8000` or the remote hetzner-staging hub
  through their CLI config?
- **Which scout** is `beaconry-scout` — is `down` referring to the
  dev-compose scout on `localhost:18001` (bb-82jq has no dev-compose,
  so this is normal) or the systemd scout on `localhost:8001` (which
  IS running + healthy, reporting as `bb-82jq` in the FLEET row below)?
- **Which host am I on** — is this output captured on bb-82jq, server2,
  or hetzner? `beaconry status` from any of them would show the same
  rows modulo the hub config.
- **Is the top block's `beaconry-scout: down` the same scout as
  FLEET's `bb-82jq`?** Not obvious either way.

The row names `beaconry-hub` and `beaconry-scout` are the LOCAL
dev-compose container names (BC-101 — `beaconry-dev-hub` + `beaconry-
dev-scout`, with the CLI still labeling them without the `-dev-`
prefix). But the probed URLs come from:

- Hub: CLI config (`~/.config/beaconry/cli.yml`) + `/healthz` — could
  be local dev-compose OR remote.
- Scout: hard-coded `http://localhost:18001/healthz` (BC-101 dev
  compose port). Never the systemd scout's `:8001`.

So on `bb-82jq`:
- "beaconry-hub running" is talking to hetzner-staging (because CLI
  config points there).
- "beaconry-scout down" is probing `localhost:18001` which isn't
  listening because bb-82jq runs a systemd scout on `:8001`, not
  the dev-compose one.
- "bb-82jq (healthy, active)" in FLEET is the systemd scout — a
  different scout than the "down" one above.

BC-118 makes every row carry its probed URL + marks the fleet row
that corresponds to the host running the CLI + explains downs that
reflect "nothing to run here" rather than "something broke".

## Trigger

Operator runs `beaconry status` (or `make status`) from any host.

## Expected outcome

- Top block gains a **URL** column immediately after NAME. Every row
  shows the full URL it probed. Example row:
  ```
  NAME            URL                                 STATE    HEALTH   …
  beaconry-hub    https://hetzner-staging.ham:8000    running  healthy  …
  beaconry-scout  http://localhost:18001/healthz      down     -        …
  ```
- A **Running on: `<hostname>`** header is printed above both tables
  so the operator sees WHICH machine is reporting. Uses
  `socket.gethostname()` (matches `HOST_NAME` env semantics — BC-013
  scout defaults).
- When the local scout is `down`, the `DETAILS` column clarifies the
  cause with one of:
  - `down (dev-compose not configured on this host)` — no
    `docker-compose.yml` locally and no `beaconry-dev-scout` container
    present.
  - `down (compose stack not up — try cd poc && docker compose up -d)`
    — compose config present but container missing (pre-BC-118
    existing message, preserved).
  - `down (unreachable at <url>)` — any other case.
- The FLEET table grows a `*` marker in the HOST column of the row
  whose `host` equals `socket.gethostname()`. Row label becomes
  `bb-82jq (you)`.
- `make status` (the make target wrapping this CLI) inherits the new
  format — no separate Makefile change needed; it's the same script.

## Out of scope

- Collapsing the top block into the FLEET table (rejected — operator
  picked the two-row layout + URL column shape).
- Topology preamble line (`local hub? local scout? remote fleet?`).
- Programmatic linkage where the top-block scout URL resolves from
  the FLEET's matching row (rejected — intentionally separate
  concepts; local scout probe is about THIS host's dev-compose,
  FLEET is about every posting scout).

## Acceptance criteria (Gherkin)

```gherkin
Feature: `beaconry status` shows what each row is actually probing

  Scenario: Running on a host with only a systemd scout (no dev-compose)
    Given `bb-82jq` runs the systemd scout only (port 8001, not 18001)
    And the CLI config points at hetzner-staging's hub
    When the operator runs `beaconry status`
    Then the output begins with `Running on: bb-82jq`
    And the hub row's URL cell shows `https://hetzner-staging.ham:8000`
    And the scout row's URL cell shows `http://localhost:18001/healthz`
    And the scout row's DETAILS cell shows `down (dev-compose not configured on this host)`
    And the FLEET row for `bb-82jq` displays as `bb-82jq (you)`

  Scenario: Running on a host with the dev-compose stack up
    Given the local dev-compose stack is running
    When the operator runs `beaconry status`
    Then the scout row shows `running` and its probed URL
    And no "(dev-compose not configured)" explanation appears
    And the FLEET row for `<hostname>` displays as `<hostname> (you)` if any

  Scenario: Running on a host that has no fleet presence
    Given the operator runs `beaconry status` from a laptop that
      never posted a scout to the hub
    When the output is read
    Then no FLEET row carries the `(you)` marker
    And the hub + scout rows still show their probed URLs

  Scenario: Hub URL displays even when hub is down
    Given the hub endpoint is unreachable
    When the operator runs `beaconry status`
    Then the hub row still prints the URL that was probed
    And STATE is `down`
```

## Capabilities delivered

- **CAP-T220: `beaconry status` URL + host + fleet-marker display.**
  Every probed endpoint is visible; the operator knows which hub the
  hub-row probes, which scout the scout-row probes, which host the
  CLI is running on, and which FLEET row represents the current
  machine. False-down on hosts without dev-compose is explained
  inline so it doesn't look like a real incident.

## Links

- BC-013 (scout live-probe HTTP endpoints — the probed-URL pattern).
- BC-055 (introduced the CLI config that drives the hub URL).
- BC-061 (scout fleet registry — the FLEET table).
- BC-101 (dev-compose container rename / port 18001 move — origin of
  the `localhost:18001` default probe URL).
