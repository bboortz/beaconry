---
id: BC-083
title: Migrate runtime to ghcr.io/bboortz/python-scratch-trixie:3.13 + base-image preflight lint
status: shipped
created: 2026-11-04
branch: feat/BC-083-forge-base-image
plan: docs/plan/BC-083-forge-base-image-plan.md
---

# BC-083: Migrate runtime to the forge base image

Operator asked to swap beaconry's runtime stage from Google
distroless (`gcr.io/distroless/python3-debian13:nonroot`) to a
self-owned image at `ghcr.io/bboortz/python-scratch-trixie:3.13`,
and (follow-up prompt) to introduce standing preflight checks for
every `FROM` in beaconry's Dockerfiles so a base-image provider
pulling the rug (made private, repo deleted, tag retagged) is a
CI failure, not an operator's silent `make live-rebuild` break.

## Operator scenario

**Who**: me (bboortz) as key user — same operator who built the
forge image. Also any future operator pulling beaconry.

**When**: on next `make live-rebuild` after the Dockerfile swap.
On every fresh install. On every `make check` (preflight lint).

**Success**:
- Hub + scout rebuild cleanly FROM the forge base.
- `make test-cold-start` passes.
- `make dogfood-scan` reports zero High+/fixable on the rebuilt
  images.
- `docker exec hub /bin/sh` still fails (no shell, BC-078 contract).
- Runtime still runs as uid 65532 — zero chown migration for
  operators who already migrated to BC-082's debian13.
- `make verify-base-images` fails loudly if any `FROM` loses
  anonymous public reachability.

**Urgency**: Tier 2 — hardening + supply-chain self-sovereignty.

## Gherkin

```
Feature: forge base image migration

Scenario: Hub + scout rebuild cleanly from forge base
  Given poc/{hub,scout}/Dockerfile runtime stage FROM
   ghcr.io/bboortz/python-scratch-trixie:3.13
  When docker compose build hub scout runs
  Then both builds succeed
   And beaconry-hub:latest + beaconry-scout:latest reach /readyz = 200
   And the images are ≤ their pre-BC-083 size

Scenario: Runtime still has no shell
  Given beaconry-hub:latest built from forge base
  When docker exec hub /bin/sh is attempted
  Then the exec fails because /bin/sh does not exist
   And /bin/bash fails too

Scenario: Runtime still runs as non-root uid 65532
  When the operator inspects the running container
  Then os.getuid() inside the container returns 65532
   And the BC-082 data volume (chowned to 65532:65532) remains writable
   And no chown migration is required

Scenario: Dogfood grype passes on the forge base
  When make dogfood-scan runs after rebuild
  Then it reports zero High/Critical findings with fix-available
   And exits 0

Scenario: Pinned-version lint accepts forge as primary, distroless as deprecated
  When make verify-pinned-versions runs with forge runtime
  Then it passes
  When run with distroless/python3-debian13:nonroot runtime
  Then it still passes (migration window)
   And the lint output warns that distroless is deprecated

Scenario: make verify-base-images catches a private forge image
  Given ghcr.io/bboortz/python-scratch-trixie:3.13 is pulled private
  When make verify-base-images runs (empty docker config, anonymous)
  Then the lint fails with a 401/404 against that tag
   And names the Dockerfile + line where the FROM lives

Scenario: verify-base-images cache prevents network-gated pre-push
  Given verify-base-images ran successfully < 24h ago
  When make check runs a second time
  Then the lint reads the cached pass + skips network calls
   And completes in <1s

Scenario: Cold-start boots within 180s on the forge base
  When make test-cold-start runs
  Then hub + scout reach /readyz = 200 within 180s
   And all 8 health endpoints return 200
```

## Capabilities

- **CAP-T169** (new): Runtime image sourced from self-owned
  `ghcr.io/bboortz/python-scratch-trixie:3.13` (Python 3.13.18,
  sys.path `/usr/local/lib/python3.13`, scratch-based, no shell,
  no system package manager).
- **CAP-T170** (new): Pre-flight inspection of a new base image
  recorded in the plan doc (user/uid, ENTRYPOINT, CMD, Python
  version, sys.path, shell presence) **before** the Dockerfile
  diff lands. Encodes "understand the image you're pulling" as
  a hard requirement.
- **CAP-T171** (new): `make verify-base-images` — standing lint.
  Enumerates every `FROM` across all Dockerfiles, runs an
  anonymous bearer-token exchange against the registry, asserts
  the manifest is 200 without creds. Fails on 401/404/5xx. 24h
  on-disk cache keeps the fast tier fast.
- Extends **CAP-T161** (distroless runtime): same no-shell,
  non-root contract, different provider.
- Extends **CAP-T166** (pinned-version floor lint): forge becomes
  primary, distroless kept as accepted-but-deprecated fallback
  during a one-release migration window.

## Soft migration

Per operator decision:
- The forge tag is the **new primary** runtime.
- Distroless `python3-debian13:nonroot` stays **accepted** by
  `verify_pinned_versions.py` for one release window
  (hard-cutoff planned for BC-08X — future). This gives any
  external operator one release to rebuild.
- Floating `:3.13` tag (not digest-pinned) per operator: auto-pulls
  security updates upstream; digest pin can land in a future BC
  once forge CI publishes digests.

## Out of scope

- Building `python-scratch-trixie` itself (forge repo).
- cosign signature verification on pull (image is signed; beaconry
  doesn't gate on that yet — future BC).
- Private-registry credential flow in `make live-rebuild`
  (not needed: forge image is public).
- Hard-cutoff of distroless support.
