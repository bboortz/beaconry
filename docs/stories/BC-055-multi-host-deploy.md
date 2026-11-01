---
id: BC-055
title: HTTP-only CLI → hub (manage from laptop, hub on hetzner-staging)
status: shipped
created: 2026-11-01
branch: feat/BC-055-multi-host-deploy
plan: docs/plan/BC-055-multi-host-deploy-plan.md
---

# BC-055: HTTP-only CLI for multi-host deployment

## Narrative

Homelab operator runs the hub on `hetzner-staging` (reachable via
Tailscale `100.x.x.x`) and wants to manage beaconry from their laptop.
Today every operator CLI (`make findings`, `make audit`, `make status`,
`make scan`) assumes it can `docker exec` into a locally-running
container — fine on the hub host, broken from the laptop.

Fix: the hub exposes an HTTP surface covering every operator action,
auth-gated by a shared bearer token. Operator CLIs auto-route to that
HTTP surface when a config file points at a non-localhost hub. No SSH,
no Docker-socket exposure on the laptop.

## Trigger

Operator wants to verify the POC on their homelab infrastructure: hub
on hetzner-staging, scout on hetzner-staging (and later on laptop),
all operator actions driven from the laptop.

## Expected outcome

- **Read endpoints** (auth-gated): `GET /findings`, `GET /audit`,
  `GET /fleet`. Reuse the existing CLI shape.
- **Control endpoint**: `POST /scan/trigger` on the hub (proxies to the
  scout's local scan trigger via Docker API). Operator doesn't need to
  reach the scout directly.
- **Mutate endpoint**: `PATCH /findings/{finding_id}` with
  `{status: "snoozed"|"accepted"|"fixed"}`. Backs the new `make set-status
  FINDING=<id> STATUS=<status>` operator CLI.
- **Bearer auth** on every endpoint except `/livez` / `/readyz` /
  `/healthz` / `/startupz`. `HUB_AUTH_TOKEN` env var. Empty = auth
  disabled (back-compat for existing single-host operators).
- **CLI config**: `~/.config/beaconry/cli.yml` carries `hub_url` +
  `hub_token`. Env + Make-vars override. Missing file → localhost
  behavior unchanged.
- **CLI routing**: non-localhost `hub_url` → HTTP; localhost + local
  compose up → `docker exec` (unchanged); else local DB file. No SSH.

## Out of scope

- SSH-based `make scan HOST=user@host` — explicitly excluded.
- Automated scout install on remote hosts — operator uses their own
  Ansible or `docker compose -f docker-compose.scout-only.yml up`.
- Streaming `make audit FOLLOW=1` over HTTP. Follow-up story.
- TLS termination — operator uses Tailscale (encrypted at transport)
  or a reverse proxy. HTTP-only on the wire inside the trust boundary.
- Mutating `make set-status` for `regressed` / `verification_failed`
  — those are system states, not operator-initiated.

## Acceptance criteria (Gherkin)

```gherkin
Feature: HTTP-only CLI → hub

  Scenario: GET /findings returns rows
    Given hub has 3 findings
    When GET /findings arrives (auth off)
    Then response is 200 with 3 JSON rows

  Scenario: GET /findings filters by status + host
    Given hub has mixed findings
    When GET /findings?status=open&host=laptop arrives
    Then only matching rows are returned

  Scenario: GET /audit returns enriched events
    Given hub has audit events
    When GET /audit?last=10 arrives
    Then response is 200 with up to 10 events newest-first

  Scenario: GET /fleet lists per-host summaries
    Given findings on hosts laptop + server2
    When GET /fleet arrives
    Then response is a JSON array with one row per host

  Scenario: POST /scan/trigger schedules a scout scan
    Given scout is up
    When POST /scan/trigger arrives on the hub
    Then response is 202 scheduled
    And a scan runs within SCAN_INTERVAL

  Scenario: PATCH /findings/{id} sets status
    Given finding X exists in state open
    When PATCH /findings/X with {"status": "snoozed"} arrives
    Then response is 200 and the DB row has status=snoozed
    And an audit event status_change is appended

  Scenario: Auth-gated when HUB_AUTH_TOKEN set
    Given HUB_AUTH_TOKEN=sekret
    When any /findings|/audit|/fleet|/scan/trigger|/findings/X call arrives without Bearer
    Then response is 401

  Scenario: Health endpoints never auth-gated
    Given HUB_AUTH_TOKEN=sekret
    When GET /livez /readyz /healthz /startupz arrive without auth
    Then response is 200 (or 503 for a real health failure)

  Scenario: findings.py auto-routes to HTTP for remote hub
    Given ~/.config/beaconry/cli.yml hub_url = http://100.x.x.x:8000
    When operator runs `make findings`
    Then findings.py GETs /findings on that URL
    And does NOT invoke docker exec

  Scenario: make set-status FINDING=X STATUS=snoozed
    Given a known finding id X on the remote hub
    When operator runs `make set-status FINDING=X STATUS=snoozed`
    Then the CLI PATCHes /findings/X with Bearer auth
    And prints the hub's confirmation
```

## Capabilities delivered

- **CAP-T109: Hub HTTP management surface** (technical).
- **CAP-T110: Operator-CLI config loader + HTTP routing** (technical).
- **CAP-T111: PATCH /findings/{id} mutation endpoint** (technical).
- **CAP-B038: Operator manages beaconry on a remote hub from their
  laptop over HTTP only** (business).

## Links

- Related: BC-030 (internet_facing — same bind-addr concern), BC-037
  (local `make scan`), BC-038 (`BEACONRY_TEST_MODE`-gated `/test/*`
  routes exist on the auth-gated side).
- Memory: `memory/never-commit-credentials` applies to the bearer token.
