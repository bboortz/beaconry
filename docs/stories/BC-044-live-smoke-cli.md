---
id: BC-044
title: Live-smoke CLI harness — `make test-cli`
status: shipped
created: 2026-11-01
branch: feat/BC-044-live-smoke-cli
plan: docs/plan/BC-044-live-smoke-cli-plan.md
---

# BC-044: Live-smoke CLI harness — `make test-cli`

## Narrative

Three stories in the last two weeks shipped bugs that only surface when
the stack is live-rebuilt and the operator exercises the real CLI /
Dockerfile / docker-exec paths:

- **BC-030** — `notifiers/` directory not COPY'd into `hub/Dockerfile`.
  Unit tests ran against host-side `poc/hub/notifiers/`. Production path
  = *zero notifications*.
- **BC-035** — `from poc.hub import notifiers as _n` inlined in notify
  functions. Host test context has `poc.hub`; container's `/app/` is flat.
  Production path = `WARNING notify failed for X: No module named 'poc'`
  on every CVE.
- **BC-042** — `make audit FOLLOW=1` buffered stdout + `SINCE=bogus`
  tracebacked through the compose-exec path. Unit tests exercised the
  host-side path only.

The pattern: *production-only code paths lack a mechanical gate.* The
scenario harness (BC-038) tests the hub ingestion flow against an
isolated hub-only stack, which catches *behavioral* regressions. It
doesn't cover operator-CLI formatting, Dockerfile COPY'd files, or
docker-exec inline-script routing. Each of those three bugs reached a
live run; one (BC-030) reached actual operator-facing noise.

`make test-cli` fills that gate.

## Trigger

Any PR that touches `poc/scripts/*.py`, `poc/hub/Dockerfile`, or the
inline docker-exec snippets inside the CLI scripts. The harness runs
against whatever state is currently live (operator chose it) — no
auto-rebuild, no destructive state change.

## Expected outcome

- `make test-cli` runs ~10 operator-visible scenarios against the live
  `beaconry-hub` + `beaconry-scout` stack.
- Each scenario asserts the structural contract an operator relies on:
  columns present, exit codes, filter semantics, formatted (not JSON)
  output on `FOLLOW=1`.
- Fails fast with "stack not running — run `make live-rebuild` first"
  when the compose stack isn't up.
- Exits 0 all-green; non-zero + one-line-per-failure on drift.
- Pattern: subclassed `Scenario`s like BC-038's scenario harness, so
  adding a new live-CLI test is one new file + one registry entry.
- Added to `make check-full` opt-in via `BEACONRY_SKIP_CLI_SMOKE=1`
  escape hatch (default: run).

## Out of scope

- Auto `make live-rebuild` before running. Operator chooses their state.
- Destructive scenarios (`make audit` modifying DB — audit is read-only
  anyway).
- Rich assertion library (pytest-style). Each scenario raises
  `AssertionError`; the runner surfaces + continues.
- Scenarios that exercise `make demo` interactively (long-running).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Live-smoke CLI harness

  Scenario: Clean state passes
    Given the live stack is running (beaconry-hub + beaconry-scout up)
    When operator runs `make test-cli`
    Then exit 0
    And output shows ✓ per scenario

  Scenario: Stack down → clear error
    Given beaconry-hub is NOT running
    When operator runs `make test-cli`
    Then exit non-zero
    And output says "stack not running — run `make live-rebuild` first"

  Scenario: `make findings` columns present
    Given stack is running
    When the harness invokes `make findings`
    Then stdout contains KEV, NET, CVE, Severity, CVSS, Host, Target, Package, Status

  Scenario: `make status` shows both services
    Given stack is running
    When the harness invokes `make status`
    Then stdout contains "beaconry-hub" AND "beaconry-scout"

  Scenario: `make audit LAST=5` returns 5 rows + header
    Given stack is running with ≥5 audit events
    When the harness invokes `make audit LAST=5`
    Then stdout has a header row AND exactly 5 body rows

  Scenario: `make audit COUNT=1` produces summary format
    Given stack is running
    When the harness invokes `make audit COUNT=1`
    Then stdout header contains EVENT, COUNT, FIRST SEEN, LAST SEEN

  Scenario: `make audit SINCE=bogus` exits non-zero cleanly (BC-042 regression guard)
    Given stack is running
    When the harness invokes `make audit SINCE=yesterday`
    Then exit is 2
    And stderr contains "time parse error" (NOT a Python traceback)

  Scenario: `make audit FOLLOW=1` emits header through pipes (BC-042 regression guard)
    Given stack is running
    When the harness invokes `make audit FOLLOW=1` with a 2s timeout
    Then stdout starts with the TIME / AGE / EVENT header
    AND lines are formatted rows (not raw JSON)

  Scenario: `make scan` returns 202 or 409
    Given stack is running
    When the harness invokes `make scan`
    Then output contains "scheduled" (202) OR "already running" (409)
```

## Capabilities delivered

- **CAP-T093: `make test-cli` live-smoke harness** — scenario-based,
  non-destructive, catches the production-only bug class.
- **CAP-B028: Operator confidence in CLI output after any PR** — the
  specific structural contracts operators rely on are mechanically
  checked.

Extends: CAP-T082 (BC-038 scenario pattern — reused), CAP-T089
(BC-042 audit CLI — now has a live gate), CAP-T046 (BC-007 findings
CLI), CAP-T052 (BC-013 status CLI).

## Links

- Design: `memory/workflow.md § Live-stack verification`
- Related: BC-038 (scenario harness — same class pattern, different
  target surface), BC-042 (`make audit` — one of the CLIs under test).
