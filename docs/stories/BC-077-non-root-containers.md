---
id: BC-077
title: Non-root hub + scout containers
status: shipped
created: 2026-11-03
branch: feat/BC-077-non-root-containers
plan: docs/plan/BC-077-non-root-containers-plan.md
---

# BC-077: Non-root hub + scout containers

Operator asked for own-attack-surface reduction with the constraint
*"Beaconry should not introduce new issues"*. Running hub + scout
as root in-container plus mounting the Docker socket means a
container-side RCE escalates to root on the host. Dropping to a
non-root user caps the blast radius at "unprivileged in container"
+ the socket's own permission check.

## Operator scenario

**Who**: operator / key user running the beaconry stack on their homelab host.

**When**: during `make live-rebuild` after pulling v0.6.1+. Fresh
deploys inherit the hardening automatically; existing deploys need
a one-shot `chown` because the pre-BC-077 `beaconry-data` volume
is owned by root.

**Success**: both containers run as uid 1000 (`beaconry`) with the
in-container `docker` group matched to the host socket's gid. CVE
scanning + remediation keep working. `/healthz`, `/scan`, Telegram
notifications, DockerAdapter fixes — all unchanged behaviour.

**Urgency**: Tier 2 security posture — not an active CVE, pre-emptive
hardening before SaaS phase.

## Gherkin

```
Feature: non-root hub + scout containers

Scenario: Fresh deploy runs non-root
  Given a clean host with Docker installed
  When the operator runs `make live-rebuild` on v0.6.1+
  Then the hub container runs as uid 1000 (user `beaconry`)
   And the scout container runs as uid 1000 (user `beaconry`)
   And the hub can read/write /data without needing root
   And both services pass /readyz

Scenario: Non-Debian host overrides docker gid
  Given the host's /var/run/docker.sock has gid 989 (Arch)
  When the operator sets `DOCKER_GID=989` in .env
   And runs `docker compose build`
  Then the in-container `docker` group gid is 989
   And the hub DockerAdapter can run `docker pull` through the socket

Scenario: Upgrading operator chowns the data volume
  Given a pre-BC-077 beaconry-data volume with root-owned files
  When the operator follows the CHANGELOG upgrade step
  Then `chown -R 1000:100 /data` runs once in a throwaway container
   And the next `make live-rebuild` boots cleanly as non-root
```

## Capabilities

- **CAP-T160** (new): Non-root hub + scout containers — Dockerfiles
  declare `USER beaconry` (uid 1000); build-arg `DOCKER_GID`
  controls the in-container `docker` group gid for socket access;
  `/app` + `/data` chowned at build time so fresh volumes inherit
  non-root ownership.

## Out of scope

- Distroless base image + multi-stage builds → **BC-078** (next).
- Removing root from the image entirely → distroless does that
  cleanly, so leaving the setup `useradd` under root for now.
- Capability drop (`--cap-drop=ALL`) → separate hardening pass.
