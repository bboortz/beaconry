---
id: BC-012
title: Shared `beaconry_health/` package — compiler-enforced trap guard, uniform containers
status: shipped
created: 2026-10-30
branch: feat/BC-012-shared-beaconry-health
plan: docs/plan/BC-012-shared-beaconry-health-plan.md
---

# BC-012: Shared `beaconry_health/` package

## Narrative

The homelab operator runs `find . -name health.py` after BC-010 ships and sees exactly one result: `scout/health.py`. Hub's four endpoints and probe helpers live inline in `hub.py`. One convention, two shapes. A third container — the imagined BC-011 target probe worker, or a hypothetical scheduler service — would need to re-read `memory/health-checks.md` and reimplement the convention a third time, with high risk of drift (one container forgets to prevent DB access in `/livez`, invites the restart-loop trap back in). BC-012 extracts the shared surface into `poc/beaconry_health/` — a single package every container COPYs. The restart-loop trap moves from "memory rule enforced by vigilance" to "typed interface — passing a DB-dependent probe to the `/livez` slot is a type error at pyright time". After BC-012: adding a new container's health surface is `COPY beaconry_health/ ./` plus three lines of configuration.

## Trigger

Developer-facing: the asymmetry noticed immediately after BC-010 ships, when the author sees that the standard document says "every container follows the convention" but the code shows only partial compliance. Also pulled forward by two downstream stories (BC-011 waterfall, BC-013 scout live probes) that both benefit from the shared primitives being in place first.

## Urgency

N/A — architectural / developer-productivity work, not a tier-triggered remediation.

## Expected outcome

- New package `poc/beaconry_health/` with four modules: `endpoints.py` (FastAPI router factory), `sentinels.py` (file-sentinel writer class), `checks.py` (reusable probes), `trap.py` (typed `LivezProvider` / `ReadyzProvider` guard stubs)
- Hub's four endpoints become `api.include_router(build_router(HubChecks()))` — behavior identical, implementation one line
- Scout's current `scout/health.py` becomes a thin config module naming scout-specific checks; the sentinel-writer plumbing moves to `beaconry_health.sentinels`
- Each container's Dockerfile has `COPY beaconry_health/ ./beaconry_health/` next to its own COPY lines
- **The compiler-as-guardrail property**: `build_router(livez_provider: LivezProvider, readyz_provider: ReadyzProvider, ...)` — a function whose argument types forbid passing a dep-checking probe (`check_db`, `check_grype_db`) into the `/livez` slot. Pyright flags it; it never reaches runtime
- All existing BC-010 tests pass unchanged — this is a behavior-preserving refactor
- Dockerfile lint test: `make check` fails if any container Dockerfile that serves health endpoints forgets `COPY beaconry_health/`
- `memory/health-checks.md § Checklist` leads with "Step 1: `COPY beaconry_health/` in your Dockerfile" instead of "implement the four endpoints"
- 1 business + 3-4 technical CAPs registered

## Out of scope

- Scout live HTTP probes — that's BC-013, built on top of BC-012
- Target-container health check waterfall — BC-011, inherits BC-012 primitives
- Docker Hardened Images sidecar pattern — IDEAS entry; irrelevant until we drop `python:3.12-slim`
- Prometheus `/metrics` endpoint — IDEAS "Observability"
- Converting scout's file-sentinel approach to HTTP in this story — BC-013
- Rewriting any BC-010 behavior — strict behavior-preservation
- Public exposure of health endpoints — stays internal / Compose network

## Acceptance criteria (Gherkin)

```gherkin
Feature: Shared beaconry_health/ package centralises the four-endpoint convention

  Background:
    Given the package poc/beaconry_health/ exists with endpoints, sentinels, checks, trap modules

  Scenario: Hub mounts endpoints via build_router — behavior unchanged from BC-010
    Given hub starts with build_router(HubChecks()) wired to its FastAPI app
    When each of /livez, /readyz, /startupz, /healthz is hit
    Then responses match the BC-010 contract byte-for-byte (status codes, JSON shape, 503 reasons)
    And the pre-BC-012 test_hub_health.py suite passes unchanged

  Scenario: Scout uses SentinelWriter — behavior unchanged from BC-010
    Given scout loop writes sentinels via beaconry_health.sentinels.SentinelWriter
    When write_live, write_ready, write_started, write_healthz run across loop iterations
    Then the sentinel files are byte-for-byte identical to BC-010 output
    And test_scout_health.py passes unchanged

  Scenario: Typed trap guard rejects dep-checking probes in the /livez slot
    Given a hypothetical caller tries build_router(livez_provider=check_db, readyz_provider=check_db)
    When pyright runs on that caller
    Then pyright emits an error naming the type incompatibility
    And the error points to LivezProvider vs ReadyzProvider type stubs

  Scenario: Dockerfile lint rule catches forgotten COPY
    Given a container Dockerfile that uses `python3 -m beaconry_health` or imports beaconry_health
    When `make check` runs
    Then if that Dockerfile does not COPY beaconry_health/, the gate fails with a named error
    And the error suggests the one-line COPY to add

  Scenario: Refactor is behavior-preserving — compose up is unchanged
    Given a cold Compose up on the BC-012 branch
    When hub + scout come up
    Then make status prints the same shape as BC-010
    And hub RestartCount stays 0 under simulated grype DB loss (restart-loop trap still prevented)
    And compose startup chain (hub /readyz → scout released) still works

  Scenario: new-container checklist in memory/health-checks.md leads with the COPY step
    When a developer reads the checklist to add a new beaconry service
    Then step 1 is "COPY beaconry_health/ in your Dockerfile" (not "implement the four endpoints")
    And step 2 is "subclass or instantiate the provided HealthChecks / SentinelWriter"
```

## Capabilities delivered

- **CAP-B016: Developer adds a new beaconry container's health surface in <10 lines** (business, active) — new. The convention is a dependency, not a reimplementation.
- **CAP-T040: Shared `beaconry_health/` package provides FastAPI router factory, sentinel writer, reusable probes, and typed trap guards** (technical, active) — new.
- **CAP-T041: Typed `LivezProvider` / `ReadyzProvider` interfaces make the restart-loop trap a type error, not a runtime pathology** (technical, active) — new. The 2026-era move from docs-as-guardrail to compiler-as-guardrail.
- **CAP-T042: Dockerfile lint gate catches forgotten `COPY beaconry_health/` at merge time, not at container-start time** (technical, active) — new.

Supersedes: BC-010's inline hub endpoints and standalone `scout/health.py` — both become thin consumers of the shared package.

## Open questions

- (none — resolved during BC-010's post-ship architectural discussion that produced the "three options → recommend B" proposal that became this story.)

## Links

- Convention: `memory/health-checks.md` (BC-012 adds the typed primitive layer below the convention)
- Prerequisite for: BC-013 (scout live HTTP probes), BC-011 (target waterfall)
- Related: BC-010 (fixes its asymmetry), lours.me sidecar pattern (IDEAS — orthogonal, future)
