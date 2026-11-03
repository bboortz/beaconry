---
id: BC-082
title: Homelab-deployment coverage — cold-start, dogfood-grype, pinned-version floor
status: shipped
created: 2026-11-03
branch: feat/BC-082-homelab-deployment-coverage
plan: docs/plan/BC-082-homelab-deployment-coverage-plan.md
---

# BC-082: Homelab-deployment coverage

Operator asked (verbatim): *"We must ensure from the beaconry side
that the homelab deployment will never fail. If it fails, we need
to learn from it."*

Audit of BC-077/BC-078/BC-079/BC-080/BC-081 revealed consistent
gaps: our test harnesses set `BEACONRY_SKIP_GRYPE_DB=1`, our
distroless tests string-grep Dockerfiles instead of booting
containers, and no gate exists to catch "beaconry's own tooling
ships a known-vulnerable embedded library" (BC-081 class).

BC-082 closes three concrete coverage gaps the recent history
proves we need:

1. **Pinned-version floor lint** — fast-tier guard. A one-liner
   Dockerfile revert can't silently reintroduce the BC-081
   vulnerable docker CLI pin.
2. **Dogfood grype gate** — full-tier guard. After rebuild, run
   grype against beaconry's own images and fail on
   Critical/High CVEs with `fix-available: true`. Catches the
   next GO-2026-XXXX against our bundled binaries before the
   operator sees a Telegram alert from beaconry about beaconry.
3. **Cold-start smoke test** — full-tier guard. Spin a fresh
   compose project with throwaway volumes and **real** grype DB
   download, assert all 8 health endpoints (hub + scout × 4) go
   green within 180 s. This is the honest "will `make
   live-rebuild` on a fresh host succeed?" probe.

## Operator / developer scenario

**Who**: developer about to push a Dockerfile change or dep bump;
operator about to run `make live-rebuild` on a fresh homelab host.

**Success**:
- `make check` (fast tier) fails loudly if `DOCKER_CLI_VERSION`
  pins drop below the BC-081 floor.
- `make check-full` (pre-push gate) fails if the rebuilt hub +
  scout images carry a High/Critical fixable CVE.
- `make check-full` fails if a fresh compose project can't bring
  both services to healthy state within 180 s.

**Urgency**: Tier 2 — pre-emptive. Every failed homelab deploy
erodes operator trust more than any single CVE.

## Gherkin

```
Feature: homelab-deployment regression coverage

Scenario: Pinned-version floor catches a stale docker CLI pin
  Given a developer edits poc/hub/Dockerfile to pin DOCKER_CLI_VERSION=27.5.1
  When they run `make check`
  Then verify-pinned-versions fails
   And the error message names the BC-081 floor (29.9.2) and the fixed Go stdlib (1.26.9)

Scenario: Dogfood scan catches a new High/Critical CVE in bundled binaries
  Given beaconry-hub:latest is rebuilt from source
  When `make dogfood-scan` runs grype against it
  Then it fails if any High+ CVE has `fix-available: true`
   And it tolerates CVEs marked `won't fix` or ones not fixable
   And `BEACONRY_SKIP_DOGFOOD=1` opts out (dev-only emergency)

Scenario: Cold-start boots a fresh stack within 180 seconds
  Given no beaconry volumes exist on the host
  When `make test-cold-start` runs
  Then both hub + scout containers reach /readyz = 200 within 180 s
   And grype DB download completes successfully
   And hub /healthz shows grype_db: ok, grype_binary: ok, db: ok
   And scout /healthz shows docker_socket: ok, images_seen > 0

Scenario: Cold-start teardown always fires
  Given cold-start test is running
  When pytest exits (success or failure or SIGINT)
  Then the beaconry-cold-start compose project is torn down
   And `make cleanup` fires (BC-080) to remove leaked images
```

## Capabilities

- **CAP-T166** (new): `verify-pinned-versions` — Dockerfile floor
  lint. Prevents accidental reverts of BC-081-class fixes.
- **CAP-T167** (new): `make dogfood-scan` — grype rescan of
  beaconry's own images, blocking gate on High+ fix-available.
- **CAP-T168** (new): `make test-cold-start` — fresh compose +
  real grype DB + health probe within 180 s. Proves `make
  live-rebuild` on a fresh host will succeed.

## Learning from BC-081

Append entry to `docs/plan/CI-FAILURES.md`:

> **BC-081 (2026-11-03) — beaconry alerted on itself.** Pinned
> `DOCKER_CLI_VERSION=27.5.1` shipped Go stdlib 1.22.11, which
> grype matched against GO-2026-6604. Reachability was zero
> (Windows-only path traversal), but the self-alert eroded
> trust in all alerts.
> **Lesson**: pinned third-party binary dependencies decay
> silently as new CVEs land against old builds. The dogfood-grype
> gate (CAP-T167) now surfaces this at pre-push time instead of
> on an operator's phone.

## Out of scope

- Catching environment-specific failures (disk full, network
  down, broken Docker daemon) — those are the operator's
  responsibility; beaconry can only prove its own artifacts work.
- Rolling pin updates (Dependabot-style for `DOCKER_CLI_VERSION`,
  base images, syft/grype). Future BC when the dogfood-grype
  gate starts firing often enough to justify automation.
