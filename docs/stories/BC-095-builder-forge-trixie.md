---
id: BC-095
title: Builder stage FROM `ghcr.io/bboortz/forge-builder-trixie:latest`
status: in-progress
created: 2026-11-07
branch: feat/BC-095-builder-forge-trixie
plan: docs/plan/BC-095-builder-forge-trixie-plan.md
---

# BC-095: Move multi-stage builder from `python:3.13-slim-trixie` to forge's signed builder

## Narrative

BC-078 → BC-083 moved beaconry's **runtime** stage onto forge-managed
images (`ghcr.io/bboortz/python-scratch-trixie:3.13`, daily-rebuilt,
cosign-signed). The **builder** stage is still upstream
`python:3.13-slim-trixie` — unsigned, random rebuild cadence, zero
shared cache with the runtime.

Forge now publishes `ghcr.io/bboortz/forge-builder-trixie:latest`
(daily, signed, description: "Shared builder for Forge scratch images
— apt-upgraded + CA certs + syft"). It ships exactly what beaconry's
builder needs out of the box: python 3.13.18 (ABI-matches the
runtime), pip, syft, CA certs, tar. Switching:

- **Scout**: deletes the entire builder prelude (2 RUN lines — curl
  install + syft install). Builder stage becomes FROM + WORKDIR +
  COPY + pip install.
- **Hub**: deletes syft install + the curl install (already covered
  by the forge base) but still needs `grype` (not in base yet) and
  the `docker` CLI static download (not in base). Net 2 RUN lines
  deleted, one curl cleanup line added.
- Extends supply-chain trust to the build stage — cosign-verifiable
  against forge's OIDC identity, same as the runtime.
- Shares a daily-patched base layer across the forge matrix (shorter
  rebuild chain when forge publishes fresh python).

## Trigger

Not event-driven. Follow-on to BC-083 (runtime migration) + BC-094
(homelab consumes signed ghcr images) — closes the last upstream
base in beaconry's container supply chain.

## Expected outcome

- `poc/hub/Dockerfile` builder stage FROM swapped to
  `ghcr.io/bboortz/forge-builder-trixie:latest`. `curl` apt'd
  temporarily for the grype + docker CLI static fetches, then
  purged. syft install step deleted (comes from the base). Runtime
  stage unchanged.
- `poc/scout/Dockerfile` builder stage FROM swapped; both the curl
  apt install and syft install deleted entirely (scout only needs
  syft, which is already there). Runtime stage unchanged.
- `make verify-base-images` (CAP-T171) picks up the new ref in its
  anonymous-pull preflight automatically — the lint scans every
  FROM.
- `make check-full` still green: scenarios + e2e + cold-start +
  dogfood-grype all rebuild the images with the new base and smoke
  them end-to-end.

## Out of scope

- **SBOM-at-builder pattern** (forge's FG-020). Beaconry's runtime
  is scratch-based, so the direct motivation (Debian backport
  version strings) doesn't apply — but a related win exists
  (separate "image regressed" from "grype DB learned a new CVE"
  during dogfood scans). Tracked as follow-up candidate in
  `docs/plan/IDEAS.md`; probable **BC-096**.
- **Asking forge to bundle grype + docker CLI** into the builder
  too. Would simplify hub's Dockerfile further (delete the remaining
  curl prelude). Separate FG ask; not blocking.
- **Multi-arch**. Follow forge's matrix.
- **Pinned builder tag** (e.g. `forge-builder-trixie:2026-11-07` or
  digest). Default `:latest` for now; pinning is a future BC.

## Acceptance criteria (Gherkin)

```gherkin
Feature: beaconry's builder stage is a forge-signed image

  Scenario: Hub Dockerfile builder uses forge-builder-trixie
    Given a checkout of beaconry on this branch
    When a reader greps poc/hub/Dockerfile for FROM lines
    Then exactly one FROM references forge-builder-trixie
      (`FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder`)
    And no `FROM python:` reference remains

  Scenario: Scout Dockerfile builder uses forge-builder-trixie
    Same as above for poc/scout/Dockerfile.

  Scenario: Builder base verifies with forge's keyless-OIDC signature
    Given `ghcr.io/bboortz/forge-builder-trixie:latest` is on the FROM chain
    When `cosign verify` runs with forge's identity
      (`https://github.com/bboortz/forge/.github/workflows/build.yml@refs/heads/main`,
       `https://token.actions.githubusercontent.com`)
    Then verification succeeds

  Scenario: Both images still build + run
    When `make check-full` runs scenarios + e2e + cold-start
    Then every stage rebuilds with the new builder
     And every service reaches /healthz = ok within smoke timeout

  Scenario: Runtime image carries syft + grype + docker (hub)
    When the built hub image is inspected
    Then `/usr/local/bin/{syft,grype,docker}` all exist with +x

  Scenario: Runtime image carries syft (scout)
    When the built scout image is inspected
    Then `/usr/local/bin/syft` exists with +x

  Scenario: `make verify-base-images` picks up the new ref
    When the lint runs
    Then its anonymous-pull preflight lists the new builder ref
     And succeeds (ghcr accepts anonymous pulls)
```

## Capabilities delivered

- **CAP-T196: Builder stage FROM forge's signed
  `forge-builder-trixie:latest`** (type: technical, status: active).
  Supply-chain trust extended to the build stage — same signing
  identity as BC-083's runtime migration. Closes the last
  upstream-base gap in beaconry's container chain.

## Breaking changes

### API / return shape
**None.** Container HTTP surfaces unchanged.

### Data model
**None.** No schema changes.

### Behavioral semantics
**None at runtime.** Build-time only edits. Resulting runtime image
is byte-equivalent to the previous build, modulo:
- Python stdlib patch level if forge's trixie apt moved between
  the previous build and this one.
- pip transitive deps (always resolved at build time; same
  resolver state would produce the same wheels either way).

**One posture change**: the builder base is now cosign-verifiable
against forge's OIDC identity. Operators who audit the build chain
gain one more signed link.

## Links

- Forge builder image: `ghcr.io/bboortz/forge-builder-trixie:latest`
  (source=`github.com/bboortz/forge`, revision `efee1310…`, created
  2026-11-07 17:14 UTC, description: "Shared builder for Forge
  scratch images — apt-upgraded + CA certs + syft").
- BC-083 (runtime migration to forge).
- BC-094 (homelab consumes forge-signed runtime images).
- CAP-T171 (`make verify-base-images`) — picks up the new ref
  automatically.
- Future **BC-096** candidate: SBOM at builder stage, consumed by
  dogfood_scan (adopts forge's FG-020 pattern). See
  `docs/plan/IDEAS.md`.
