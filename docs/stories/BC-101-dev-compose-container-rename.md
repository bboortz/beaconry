---
id: BC-101
title: Rename dev-compose containers to avoid collision with operator systemd scout
status: shipped
created: 2026-11-08
branch: feat/BC-101-dev-compose-container-rename
plan: docs/plan/BC-101-dev-compose-container-rename-plan.md
---

# BC-101: `beaconry-{hub,scout}` → `beaconry-dev-{hub,scout}` in dev compose

## Narrative

On 2026-11-08 ~21:20 UTC, during work on BC-099/BC-100, the operator's
systemd-managed scout on bb-82jq went dark for ~55 minutes without
anyone noticing. Root cause: silent container-name collision.

- `poc/docker-compose.yml` names its containers `beaconry-hub` +
  `beaconry-scout` (dev stack, for local testing).
- `poc/docker-compose.scout-only.yml`, which the systemd service
  `beaconry-scout.service` wraps (via `/home/benni/git/homelab/systemd/
  beaconry-scout/*`), names its container `beaconry-scout` (operator
  production scout — POSTs to hetzner).

Both compose files want the same Docker container name. When a
developer runs `docker compose up -d` in `poc/`, Docker creates
`beaconry-scout` against the dev config. When the developer runs
`docker rm -f beaconry-scout` to clean up (very common during
testing), the systemd service's container goes down too. Systemd
observes a clean exit (status=0/SUCCESS) and marks the service
"Deactivated successfully" — no alert, no restart. The operator's
production scout stays dark until someone notices (54 minutes in
this incident).

Symptom visible in the operator view: bb-82jq FLEET row showed `⚠
stale`, `last_heartbeat 54m ago`, and all 17 open + 7 regressed
findings frozen (BC-086 reconcile can't fire without scout POSTing).
Restarting the systemd service recovers; auto-close fires normally
over the next 3 scan cycles.

BC-101 removes the collision by renaming the dev-compose containers.
The systemd/operator scout keeps the unqualified `beaconry-scout`
name (that's what ansible + homelab expect); the dev containers
become `beaconry-dev-{hub,scout}`.

## Trigger

- 2026-11-08 ~21:20 UTC incident: 17 open + 7 regressed findings
  frozen on bb-82jq for ~55 min because `docker rm -f
  beaconry-scout` during BC-099 cold-start troubleshooting
  accidentally killed the systemd-managed container.
- The design gap is structural: any future `docker compose up -d`
  in `poc/` on an operator host will produce the same silent
  takeover. Needs a one-time rename, not more documentation.

## Expected outcome

- `poc/docker-compose.yml` container_name updates:
  - `beaconry-hub` → `beaconry-dev-hub`
  - `beaconry-scout` → `beaconry-dev-scout`
- `poc/docker-compose.scout-only.yml` **unchanged** —
  `beaconry-scout` stays the operator/production name (ansible roles
  + homelab playbooks expect this).
- Makefile targets that `docker` -reference these by name
  (`make live-rebuild`, `make live-status`, `make status`'s local
  check) updated to the new dev names.
- `poc/scripts/cleanup.py` allowlist: no change — the live-stack
  images (`beaconry-hub:latest`, `beaconry-scout:latest`) are tagged
  by image name, not container name, so cleanup rules still apply.
- `poc/tests/*` that reference container names (`beaconry-cold-start-*`
  already distinct; `beaconry-hub-test`, `telegram-mock` already
  distinct) — review + update any that use `beaconry-hub` or
  `beaconry-scout` literally.
- `docker ps` on an operator machine after BC-101 can show BOTH
  `beaconry-scout` (systemd/operator, POSTing to hetzner) AND
  `beaconry-dev-scout` (dev-compose, POSTing to local hub) at the
  same time without collision.

## Out of scope

- Renaming any production/ansible side — the operator systemd stack
  keeps its current names. Changing those ripples into homelab,
  server2, hetzner-staging configs.
- Fixing the hub-side "scout gone silent → findings freeze" gap.
  Separate concern (parked as a BC-102 candidate from the earlier
  investigation).
- Renaming the cold-start compose containers (already distinct
  with the `beaconry-cold-start-` prefix).
- Renaming the e2e test containers (already distinct:
  `beaconry-hub-test`, `telegram-mock`, `probe-target-test`).
- Add-only renames (`beaconry-scout-dev`) — BC-101 rewrites the
  config, no new container names appear outside the `*-dev-*`
  pattern.
- CLI-config changes — `~/.config/beaconry/cli.yml` points at
  hetzner hub URL (BC-055 pattern); container names are irrelevant
  to the CLI.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Dev and operator scout containers coexist without collision

  Scenario: poc/docker-compose.yml container_name is renamed
    Given a checkout of beaconry on this branch
    When a reader greps poc/docker-compose.yml for `container_name:`
    Then the hub service uses `beaconry-dev-hub`
     And the scout service uses `beaconry-dev-scout`

  Scenario: Operator systemd scout config is unchanged
    When a reader greps poc/docker-compose.scout-only.yml
    Then the scout service's container_name is still `beaconry-scout`

  Scenario: Dev stack and operator scout can run concurrently
    Given beaconry-scout.service (systemd) is active
      And docker ps shows a container named `beaconry-scout`
    When the developer runs `cd poc && docker compose up -d`
    Then `docker ps` shows BOTH `beaconry-scout` AND `beaconry-dev-scout`
     And no container is restarted or removed

  Scenario: `docker rm -f beaconry-dev-scout` does not affect operator scout
    Given both containers are running
    When `docker rm -f beaconry-dev-scout` is executed
    Then the operator `beaconry-scout` container stays up

  Scenario: Makefile local-stack targets use the new names
    When a reader greps the Makefile for container-name references
    Then `make live-rebuild` / `make live-status` / `make status` all use
         `beaconry-dev-hub` + `beaconry-dev-scout`

  Scenario: `make check-full` passes end-to-end
    When the full tier runs
    Then every target that spins up the dev compose stack reaches healthy
     And no BC-101-induced regression is observed

  Scenario: Pre-existing test harnesses are unaffected
    When a reader greps poc/tests/**/*.py for literal container names
    Then no `beaconry-hub` or `beaconry-scout` literal remains that refers
         to the dev-compose containers (cold-start + e2e names are
         already distinct and unaffected)
```

## Capabilities delivered

- **CAP-T203: Dev-compose container names namespaced as `beaconry-dev-*`
  to avoid collision with the operator systemd scout** (type:
  technical, status: active). Removes the 54-minute-silent-outage
  class of bug from BC-099 incident.

## Open questions

1. **Does hetzner / server2 run `docker-compose.yml`?** If yes, their
   container names change too — and any monitoring that greps for
   literal `beaconry-hub` on those hosts breaks. Investigation in
   plan doc.
2. **Does `make deploy-beaconry` (homelab) rely on container names?**
   Ansible roles typically address services by image name + compose
   project, not container name. Needs a quick audit of
   `~/git/homelab/ansible/roles/beaconry_{hub,scout}/`.
3. **Health-check wording in `memory/docker-standard.md` / `health-checks.md`?**
   Templates mention service-generic names; no literal `beaconry-hub`
   AFAICT. Confirm in plan doc.

## Links

- Incident: 2026-11-08 ~21:20 UTC, bb-82jq, 17 open + 7 regressed
  findings frozen 54 min.
- Related gap (parked as BC-102 candidate): hub-side stale-scout
  sweep so an offline scout doesn't freeze findings indefinitely.
- `poc/docker-compose.yml` — current dev stack
- `poc/docker-compose.scout-only.yml` — operator systemd wraps this
- `/etc/systemd/system/beaconry-scout.service` — operator scout
  service (ansible-managed via homelab)
- `/home/benni/git/homelab/systemd/beaconry-scout/` — service file
  source
