---
id: BC-040
title: Three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
status: shipped
created: 2026-10-31
branch: feat/BC-040-three-way-env-sync
plan: docs/plan/BC-040-three-way-env-sync-plan.md
---

# BC-040: Three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml

## Narrative

Developer at desk, 2026-10-31: *"BC-039 catches code↔doc drift. BC-011 catches hub-code↔compose drift. But three operator-facing surfaces touch env variables — `VARIABLES.md` (doc), `.env.example` (operator starter config), `docker-compose.yml` (what propagates to the container) — and none of the existing lints assert they stay in sync with each other."*

Concrete gap: `.env.example` today ships with 7 variables; `VARIABLES.md` has 32. An operator who copies `.env.example` to `.env` and runs beaconry has NO exposure to any of the Heartbeat, Digest, Reconcile, or Internet-facing dials unless they stumble across a BC story or read `VARIABLES.md`.

Second gap: a variable could be in all three files but with mismatched defaults (e.g. `MIN_CVSS=9.0` in `.env.example` while `VARIABLES.md` says `7.0`). The operator trusts `.env.example`, runs with 9.0, misses a CVSS 7.5 KEV finding, and never knows why.

## Trigger

Any `.env.example` edit, any new variable row in `VARIABLES.md`, any new `environment:` entry in `docker-compose.yml`. The lint runs as part of `make check`.

## Expected outcome

- `.env.example` lists **every** runtime variable (except pure dev-only ones that have no business on an operator's machine — call out exceptions).
- `.env.example` style matches `.env` (grouped by concern, `VAR=default` with aligned `# default | description` comment column).
- Required-no-default variables use angle-bracket placeholders (`TELEGRAM_BOT_TOKEN=<from @BotFather>`).
- `make verify-variables` fails CI on:
  - Variable in `VARIABLES.md` but missing from `.env.example` (unless intentionally omitted — explicit allowlist).
  - Variable in `.env.example` but missing from `VARIABLES.md`.
  - Variable in `docker-compose.yml`'s hub/scout `environment:` list but missing from `VARIABLES.md`.
  - Default mismatch between `.env.example` and `VARIABLES.md` when both carry a concrete literal.

## Out of scope

- Rewriting `docker-compose.yml`. Existing env propagation already covered by BC-011; this story relies on it.
- Documenting system vars (`PATH`, `HOME`).
- Generating `.env.example` from `VARIABLES.md`. Hand-maintained with a lint is simpler + PR-reviewable (same reasoning as BC-039).
- Changing the lint's `TEST_ONLY_ALLOWLIST`. Those vars stay out of `.env.example` by design.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Three-way variable sync

  Scenario: Clean state passes
    Given VARIABLES.md ↔ .env.example ↔ docker-compose.yml are aligned
    When `make verify-variables` runs
    Then exit 0

  Scenario: Variable in VARIABLES.md but missing from .env.example
    Given `BEACONRY_NEW_DIAL` is documented and read in code
    And `.env.example` has no line for it
    When `make verify-variables` runs
    Then exit non-zero
    And output shows a copy-pasteable `.env.example` line

  Scenario: Variable in .env.example but no VARIABLES.md row
    Given `.env.example` has `BEACONRY_TYPO=1`
    And no VARIABLES.md row for `BEACONRY_TYPO`
    When `make verify-variables` runs
    Then exit non-zero
    And output names the orphan

  Scenario: Default mismatch
    Given VARIABLES.md says `MIN_CVSS` default is `7.0`
    And `.env.example` has `MIN_CVSS=9.0`
    When `make verify-variables` runs
    Then exit non-zero
    And output names both values + the resolution hint

  Scenario: Angle-bracket placeholder is skipped on default comparison
    Given `.env.example` has `TELEGRAM_BOT_TOKEN=<from @BotFather>`
    And VARIABLES.md says default is `*(empty)*`
    When `make verify-variables` runs
    Then no violation is raised (placeholder is operator-filled, no equality check)

  Scenario: Variable propagated via compose but no VARIABLES.md row
    Given `docker-compose.yml` has `- BEACONRY_SOMETHING=${BEACONRY_SOMETHING:-}` under hub.environment
    And no VARIABLES.md row for it
    When `make verify-variables` runs
    Then exit non-zero
    And output names the compose-only entry

  Scenario: Developer-only variable allowlisted out of .env.example
    Given `BEACONRY_SCENARIO_HUB_URL` is on the dev-only-allowlist for .env.example
    When `make verify-variables` runs
    Then no violation for its absence from .env.example
```

## Capabilities delivered

- **CAP-T086: Rewritten `poc/.env.example` as the full operator catalog** — every runtime variable (minus dev-only allowlisted ones) with the same `VAR=default # default | description` format as `.env`.
- **CAP-T087: Three-way lint — `VARIABLES.md` ↔ `.env.example` ↔ `docker-compose.yml`** — extends `verify_variables_documented.py` with env-file parse + compose-env parse + default-comparison; strict literal match with wrapper-stripping for prose defaults.
- **CAP-B024: Operator who copies `.env.example` gets every dial, with correct defaults** — zero spelunking required to discover BC-035/036/030 toggles.

Extends: CAP-T084/T085 (BC-039), CAP-T067 (BC-011 compose env propagation).

## Links

- Design: `memory/code-quality.md`
- Related: BC-039 (code↔doc), BC-011 (hub-code↔compose), BC-016 (docs drift).
