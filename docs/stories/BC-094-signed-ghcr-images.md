---
id: BC-094
title: Homelab consumes forge-signed ghcr images for beaconry
status: in-progress
created: 2026-11-06
branch: feat/BC-094-signed-ghcr-images
plan: docs/plan/BC-094-signed-ghcr-images-plan.md
---

# BC-094: Homelab consumes forge-signed ghcr images

## Narrative

Homelab's `make deploy-beaconry` currently runs `docker compose build
--pull` on every host — building both hub and scout images locally.
Meanwhile forge (FG-NNN series) already publishes cosign-signed
`ghcr.io/bboortz/beaconry-{hub,scout}` images keyless-OIDC against
its `build.yml@refs/heads/main` identity. The pre-built artifact
exists and is signed; homelab just doesn't consume it.

Consequences of the status quo:
- Local-build latency per deploy (~1-2 min hub, ~30s scout).
- Each host's build depends on its own docker daemon state → two
  "beaconry at HEAD" hosts can actually run different bits.
- No signature verification path (nothing cosign-verifies before
  `up -d`).
- Rollback story is "git checkout <ref> + rebuild" rather than "set
  one env + pull + restart".

BC-094 flips **homelab** (not local dev) to consume the forge
artifact. The beaconry repo contributes two tiny override compose
files (`docker-compose.{hub,scout}-ghcr.yml`) + one env var
(`BEACONRY_IMAGE_TAG`). Local dev's `make demo` / `make
live-rebuild` continues to build from source — override files are
opt-in via `docker compose -f …` chain.

## Trigger

Nothing event-driven in beaconry. The homelab side's trigger is
ansible's `make deploy-beaconry` — but that's a follow-up PR against
the homelab repo. This BC's beaconry-side scope is purely the
override files + env + docs.

## Expected outcome (beaconry side only)

- `poc/docker-compose.hub-ghcr.yml` (new): override that sets
  `services.hub.image: ghcr.io/bboortz/beaconry-hub:${BEACONRY_
  IMAGE_TAG:-latest}`. Nothing else — compose merges cleanly over
  `docker-compose.yml`.
- `poc/docker-compose.scout-ghcr.yml` (new): same for scout; merges
  over `docker-compose.scout-only.yml`.
- `poc/.env.example` documents `BEACONRY_IMAGE_TAG=latest`.
- `docs/VARIABLES.md` documents the var (type: tag string, default
  `latest`, used by compose at config time — not read in Python).
- `poc/scripts/verify_variables_documented.py` gains a
  `DEPLOY_ONLY_ALLOWLIST` for compose-substituted vars so the
  orphan check doesn't flag `BEACONRY_IMAGE_TAG` ("documented but
  not read in Python").
- Local dev: completely unchanged. Running `docker compose build`
  / `docker compose up` without the ghcr override still tags
  locally as `beaconry-{hub,scout}:latest`. All downstream scripts
  (`cleanup.py`, `dogfood_scan.py`, `demo.sh`) keep working.

## Out of scope

- The homelab-side change (new ansible task, cosign verify step,
  systemd unit tweak). Separate PR against the homelab repo;
  summary attached to this BC on PR open.
- Publishing images from beaconry's own CI. Forge is authoritative.
- Multi-arch. Follow whatever forge builds.
- Signing with a static key.

## Acceptance criteria (Gherkin)

```gherkin
Feature: optional ghcr override for homelab deploys

  Scenario: ghcr override exists for hub + scout
    Given a checkout of beaconry on this branch
    When a reader enumerates poc/docker-compose*.yml
    Then docker-compose.hub-ghcr.yml and docker-compose.scout-ghcr.yml exist
     And each defines exactly one service (hub / scout respectively)
     And no `build:` key in either override

  Scenario: override resolves to the ghcr ref with BEACONRY_IMAGE_TAG
    Given BEACONRY_IMAGE_TAG is unset
    When `docker compose -f poc/docker-compose.yml -f poc/docker-compose.hub-ghcr.yml config` runs
    Then services.hub.image == "ghcr.io/bboortz/beaconry-hub:latest"

  Scenario: override honours BEACONRY_IMAGE_TAG pin
    Given BEACONRY_IMAGE_TAG=v0.16.1
    When `docker compose config` runs with the override
    Then services.hub.image == "ghcr.io/bboortz/beaconry-hub:v0.16.1"

  Scenario: local dev path unchanged without override
    Given a developer runs `make live-rebuild` in the repo
    Then `docker build` runs against the local Dockerfiles
     And the resulting image is tagged `beaconry-hub:latest` (no ghcr prefix)
     And `dogfood_scan.py` / `cleanup.py` still find it by that name

  Scenario: VARIABLES.md documents BEACONRY_IMAGE_TAG
    When `make check` runs the variables-doc lint
    Then verify-variables passes
     And BEACONRY_IMAGE_TAG appears in docs/VARIABLES.md
     And it appears in poc/.env.example
     And the lint does not flag it as an orphan (DEPLOY_ONLY_ALLOWLIST)
```

## Capabilities delivered

- **CAP-T194**: `poc/docker-compose.{hub,scout}-ghcr.yml` override
  files published; homelab opts in via `-f` chain. Local dev
  unchanged. (type: technical, status: active)
- **CAP-T195**: `BEACONRY_IMAGE_TAG` env var (default `latest`)
  documented in VARIABLES.md + .env.example. Lint allows the var
  as deploy-time-only (not Python-read). (type: technical,
  status: active)

## Links

- Forge build + sign workflow: `bboortz/forge:.github/workflows/build.yml`
- Homelab deploy (feat/beaconry-deploy): `bb-claw/homelab`
- `memory/design-decisions.md § Separation of concerns` (scout
  read-only / executor adapters) — unrelated but relevant to the
  deploy posture.
