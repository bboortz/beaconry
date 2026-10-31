---
id: BC-039
title: Document every environment variable with a drift lint
status: shipped
created: 2026-10-31
branch: feat/BC-039-env-variables-doc
plan: docs/plan/BC-039-env-variables-doc-plan.md
---

# BC-039: Document every environment variable with a drift lint

## Narrative

Developer at desk 2026-10-31: *"we've shipped ~30 env variables in the last two weeks (BC-030, BC-031, BC-035, BC-036, BC-037, BC-038 each added their own). `.env.example` is partial, `docker-compose.yml` has its own set, `memory/*.md` scatters the rationale, hub.py has the authoritative defaults. I can't tell what any one variable does without greping multiple files."*

The problem: no single source of truth. Operators discover variables by accident (e.g. "how do I tune digest cadence?" needed the full BC-036 context to answer). Developers reviewing a PR can't tell if a new variable belongs or if an existing one covers the use case.

BC-039 ships `docs/VARIABLES.md` as the one authoritative index + a `verify-variables` lint that fails CI if a variable is read in code but missing from the index (or vice versa).

## Trigger

Any `os.environ.get("...")` or `os.environ["..."]` call added to `poc/` code. The lint runs as part of `make check`.

## Expected outcome

- `docs/VARIABLES.md` lists every runtime env variable the project reads, grouped by concern.
- Each entry carries: name, default, type, service(s), purpose, BC origin, link to `.env.example` line + `docker-compose.yml` line.
- `make verify-variables` passes if the doc matches reality; fails with a copy-pasteable template entry if a code read is missing from the doc.
- `make verify-variables` catches the reverse: an entry documented but no code reads it (dead doc).
- Running `make help` shows the new target.
- `make check` (fast tier) includes `verify-variables`.

## Out of scope

- Documenting shell env vars not read by Python (e.g. `PATH`, `TERM`).
- Documenting variables used only in tests (`BEACONRY_FOO`, `BEACONRY_MISSING`, `BEACONRY_X` — these are fixtures that assert env parsing works and are explicitly tagged as test-only).
- Enforcing that `.env.example` matches `docker-compose.yml` matches `VARIABLES.md` three-way — that's a BC-011 extension; BC-039 is one-way (code → doc).
- Generating `VARIABLES.md` from code annotations (compile-time magic). Hand-maintained with a lint gate is simpler and reviewable in PRs.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Environment variable documentation drift lint

  Scenario: Variable read in code but missing from VARIABLES.md
    Given a new `os.environ.get("BEACONRY_NEW_FLAG")` call in `poc/hub/hub.py`
    And no corresponding entry in `docs/VARIABLES.md`
    When `make verify-variables` runs
    Then it exits non-zero
    And output includes a copy-pasteable template for the missing entry

  Scenario: Variable documented but not read anywhere
    Given `docs/VARIABLES.md` lists `BEACONRY_LEGACY_FLAG`
    And no `os.environ.get("BEACONRY_LEGACY_FLAG")` or `os.environ["BEACONRY_LEGACY_FLAG"]` in poc/
    When `make verify-variables` runs
    Then it exits non-zero
    And output flags the orphan entry

  Scenario: Clean state — all variables documented, all documented variables read
    Given shipped repo state 2026-10-31
    When `make verify-variables` runs
    Then it exits zero

  Scenario: Test-only variables are allowed on an explicit allowlist
    Given `BEACONRY_FOO` is referenced only in `poc/tests/`
    And it is on the test-only allowlist
    When `make verify-variables` runs
    Then no violation is reported

  Scenario: `make check` enforces the gate
    Given a violation introduced
    When operator runs `make check`
    Then the verify-variables step fails and halts the fast tier
```

## Capabilities delivered

- **CAP-T084: `docs/VARIABLES.md` — single source of truth for runtime env variables** (technical, active) — one table, grouped by concern, with name/default/type/service/purpose/BC-origin per row.
- **CAP-T085: `verify-variables` lint catches code↔doc drift bidirectionally** (technical, active) — fails `make check` on undocumented or orphaned variables; copy-pasteable fix template.
- **CAP-B023: Developer can review the entire variable surface without greping the codebase** (business, active) — serves PR reviewers, new contributors, operators tuning configuration.

Extends: CAP-T050 series (BC-016 docs drift, BC-011 compose-env propagation — same lint pattern, different axis).

## Links

- Design: `memory/code-quality.md`
- Related: BC-011 (compose env propagation — same shift-left pattern), BC-016 (verify-docs-drift — same bidirectional lint shape).
- Followup in IDEAS: three-way enforcement that `VARIABLES.md` ↔ `.env.example` ↔ `docker-compose.yml` stay aligned.
