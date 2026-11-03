---
id: BC-078
title: Distroless runtime image for hub + scout
status: shipped
created: 2026-11-03
branch: feat/BC-078-distroless-images
plan: docs/plan/BC-078-distroless-images-plan.md
---

# BC-078: Distroless runtime image for hub + scout

Follow-up to BC-077 (non-root). The runtime image still ships
`apt`, `curl`, a full `/bin/bash`, and ~70 MB of debian base
layer the beaconry process never calls. If an attacker reaches
RCE through the Python layer, those extras are the first thing
they reach for. Dropping to a distroless runtime removes them:
no shell, no package manager, no coreutils.

## Operator scenario

**Who**: operator / key user, same as BC-077.

**When**: `make live-rebuild` after pulling v0.7.0. Fresh deploys
inherit the hardening. BC-077 upgraders chown data volume once
more (1000:100 → 65532:65532).

**Success**: both containers run from distroless python3 image.
No `/bin/sh` or `/bin/bash` exists inside. CVE scanning,
remediation, notifications, FLEET view, metrics — all unchanged.
`docker exec beaconry-hub /bin/sh` fails (there is no shell).

**Urgency**: Tier 2 security posture — defense in depth, not an
active CVE.

## Gherkin

```
Feature: distroless runtime for hub + scout

Scenario: Hub image contains no shell
  Given the hub image is built from v0.7.0
  When an attacker (or operator) runs `docker exec beaconry-hub /bin/sh`
  Then the exec call fails because /bin/sh does not exist in the image
   And `docker exec beaconry-hub /bin/bash` fails for the same reason

Scenario: Hub still runs CVE pipeline
  Given the hub builds from distroless
  When scout POSTs an SBOM
  Then the hub runs grype against it (via /usr/bin/grype installed in
       the builder stage and copied into the runtime stage)
   And Telegram notifications fire as before

Scenario: Non-Debian host overrides docker gid
  Given the host's docker socket has gid 989 (Arch)
  When the operator sets `DOCKER_GID=989` in .env
   And runs `docker compose up -d`
  Then the hub container joins supplementary gid 989 via compose group_add
   And can call `docker pull` without rebuilding the image

Scenario: Upgrading operator chowns the data volume to 65532
  Given a BC-077 beaconry-data volume with files owned by 1000:100
  When the operator follows the v0.7.0 CHANGELOG upgrade step
  Then `chown -R 65532:65532 /data` runs once in a throwaway container
   And the next `make live-rebuild` boots cleanly on distroless
```

## Capabilities

- **CAP-T161** (new): Distroless runtime image — multi-stage
  build; builder stage retains `apt`/`curl` to install syft +
  grype + Python deps; runtime stage is
  `gcr.io/distroless/python3-debian12:nonroot` (uid 65532, no
  shell); binaries + site-packages copied across.

## Out of scope

- Capability drop (`--cap-drop=ALL`) → separate hardening pass.
- Read-only root filesystem (`read_only: true` in compose) →
  needs `/tmp` + `/data` tmpfs plumbing; separate story.
- Image signing (cosign) → separate story.
- Switching to a Chainguard image — Google distroless is Google-
  maintained, free, no auth. Chainguard works but adds vendor
  dependency we don't need yet.
