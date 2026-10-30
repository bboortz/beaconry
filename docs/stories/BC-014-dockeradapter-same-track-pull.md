---
id: BC-014
title: DockerAdapter same-track pull — no silent major-version jumps
status: shipped
created: 2026-10-30
branch: feat/BC-014-dockeradapter-same-track-pull
plan: docs/plan/BC-014-dockeradapter-same-track-pull-plan.md
---

# BC-014: DockerAdapter same-track pull

## Narrative

The homelab operator runs `postgres:16-alpine` in production. They tap 🔧 Fix now on a Telegram alert. BC-009 shipped the real fix execution; `DockerAdapter.execute` currently does `docker pull postgres:latest`. On today's Docker Hub that resolves to postgres 18 — a two-major-version jump that silently incompat the on-disk data format. Operator wakes up tomorrow, postgres won't start, data is in postgres-16 WAL format, service is dead until manual rollback. The CVE is gone from the image; the service is gone from existence. BC-014 fixes this by preserving the original tag: `postgres:16-alpine` → `docker pull postgres:16-alpine`. Fluid tags (`16-alpine`, `alpine`, `stable`) still pick up upstream patch updates via Docker Hub's tag-mutability. Pinned tags (`16.1.3-alpine`) stay unchanged — operator sees "upstream still vulnerable" and decides when to manually bump.

## Trigger

Operator taps 🔧 Fix now on a container finding. Current BC-009 behavior pulls `:latest`. BC-014 preserves the source_ref tag.

## Urgency

N/A — safety refinement of BC-009, not tier-triggered.

## Expected outcome

- `DockerAdapter.describe(finding)` returns `docker pull <finding.source_ref>` (the exact tag the operator runs)
- `DockerAdapter.execute(finding)` runs `docker tag <source_ref> <base>:rollback` + `docker pull <source_ref>` — rollback tag unchanged from BC-009
- Fluid-tag images (`postgres:16-alpine`, `nginx:alpine`, `redis:7`) pick up upstream patch updates when the tag mutates (standard Docker Hub behavior)
- Pinned-tag images (`postgres:16.1.3-alpine`) stay unchanged on fix; `verify_fix` surfaces "upstream still present" if the CVE isn't patched in that specific tag — operator decides manually
- BC-009 CAP descriptions updated: hardcoded `:latest` is no longer accurate
- README documents the behavior change

## Out of scope

- Registry-API tag enumeration (e.g. "find next 16.X-alpine") — needs Docker Hub v2 API client; parked
- Operator-configurable per-image tag policy — parked
- Digest pinning (`@sha256:…`) — part of "KEV auto-fix is scary" IDEAS entry
- Target-container health waterfall (BC-011)
- Changing the rollback-tag mechanism — BC-009's `<base>:rollback` tag preserved verbatim

## Acceptance criteria (Gherkin)

```gherkin
Feature: DockerAdapter preserves the source_ref tag on pull

  Scenario: postgres:16-alpine → pull postgres:16-alpine (not :latest)
    Given a finding with source_ref="postgres:16-alpine"
    When DockerAdapter.describe(finding) is called
    Then it returns "docker pull postgres:16-alpine"
    When DockerAdapter.execute(finding) runs in MOCK mode
    Then the pull command is "docker pull postgres:16-alpine"
    And the rollback-tag command is "docker tag postgres:16-alpine postgres:rollback"

  Scenario: fluid tag (nginx:alpine) → pull nginx:alpine
    Given a finding with source_ref="nginx:alpine"
    When DockerAdapter.describe(finding) is called
    Then it returns "docker pull nginx:alpine"

  Scenario: pinned tag (postgres:16.1.3-alpine) → pull same pinned tag
    Given a finding with source_ref="postgres:16.1.3-alpine"
    When DockerAdapter.execute(finding) runs
    Then it pulls postgres:16.1.3-alpine (not :latest, not 16.X-alpine)

  Scenario: source_ref without tag (nginx) → pull nginx:latest as fallback
    Given a finding with source_ref="nginx"
    When DockerAdapter.describe(finding) is called
    Then it returns "docker pull nginx:latest"
    And the fallback is explicitly documented as "no tag specified"

  Scenario: rollback tag survives after fix
    Given a successful fix on postgres:16-alpine
    When "docker image inspect postgres:rollback" is run
    Then it returns the pre-fix image id for postgres:16-alpine
    And the operator can restore via `docker tag postgres:rollback postgres:16-alpine`
```

## Capabilities delivered

- **CAP-T046: DockerAdapter preserves the source_ref tag on pull** (technical, active) — new. Same-track pull replaces hardcoded `:latest`; fluid tags still get upstream updates; pinned tags stay pinned.

Updates: CAP-T030 (rollback tag mechanism unchanged, but surrounding `:latest` text in the description becomes stale), CAP-B014 (BC-009 story's "pull `:latest`" wording).

## Open questions

- (none — resolved during post-BC-009 architectural discussion that produced the "same-track vs :latest" decision)

## Links

- Supersedes: BC-009's hardcoded `:latest` behavior
- Related: BC-011 (target health waterfall — catches "service didn't come back up"; BC-014 reduces the class of failures BC-011 has to catch)
- IDEAS: "KEV auto-fix is scary" (digest pinning, further hardening — depends on BC-014 as foundation)
