---
id: BC-001
title: End-to-end mocked POC demo (first key-user proof)
status: ready
created: 2026-10-29
branch: feat/BC-001-mocked-e2e-demo
plan: docs/plan/BC-001-mocked-e2e-demo-plan.md
---

# BC-001: End-to-end mocked POC demo (first key-user proof)

## Narrative

A homelab self-hoster — technical, runs Docker Compose stacks (Nextcloud, Jellyfin, Vaultwarden), subscribes to CVE mailing lists but hates triaging them — clones the beaconry repo. They have read the design doc and are intrigued but sceptical. They want **proof in 2 minutes** that the pipeline really does what the design claims, without having to configure a Telegram bot, pull a real nginx image, or trust that any of their infrastructure changed. They type `make demo`. Within 90 seconds they see the full pipeline execute in their terminal — scan, deliver, urgency decision with visible reasoning, two fix paths (one auto, one operator-confirmed via `Enter`), rendered notification, verification re-scan clean. Every mocked action is clearly labelled `[mock]` so nothing looks real. They finish the demo thinking "yes, this is what my life becomes" and start reading `BC-000-repo-scaffold-plan.md` to see how to contribute.

## Trigger

Developer or evaluator runs `make demo` (or `python -m poc.demo`) in a fresh clone with the dev venv active, no `.env` file, no Docker daemon running.

## Expected outcome

- 60–120 seconds of readable terminal output showing every pipeline stage with explicit labels
- Two findings processed: one Tier 0 safe-class (auto-fixed) and one Tier 1 (operator-confirmed via `Enter`)
- Each urgency decision prints its reasoning (`exposure`, `fix class`, `tier`)
- Mocked external actions are prefixed `[mock]` on screen — never ambiguous whether something real happened
- Rendered Telegram message appears in the terminal in a visible box (same format `format_notification` would produce)
- Verification re-scan runs and prints `✓ fixed and verified`
- Zero network calls; `pytest` + `coverage` + `pyright` + `ruff` still pass; `pytest` suite covers the demo path and the unit helpers it exercises

## Out of scope

- Real syft, grype, Docker socket, or Telegram API calls
- CISA KEV feed integration (V2 per `memory/design-decisions.md`)
- Full exposure scoring with real port scans (fixture-only in this story)
- Multi-host canary ring (V2)
- Health-check waterfall beyond a stub that passes
- Pacman SBOM path (POC covers it but it's not part of the demo narrative)
- OSS release packaging
- Dependency-injection refactor of the hub/scout internals — mocking is env-flag gated for BC-001 (see plan doc)

## Acceptance criteria (Gherkin)

```gherkin
Feature: End-to-end mocked POC demo

  Background:
    Given a fresh clone of the repo
    And requirements-dev.txt installed in a venv
    And BEACONRY_MOCK is unset (demo entry point sets it internally)

  Scenario: Full pipeline runs mocked in under 120 seconds
    When the developer runs "make demo"
    Then within 120 seconds the terminal output contains, in order:
      | stage                                     |
      | ==> Scout: scanning fixture inventory     |
      | ==> Hub: SBOM received                    |
      | ==> Finding: CVE-                         |
      | tier: Tier 0 (safe class, auto-fix)       |
      | tier: Tier 1 (operator confirmation)      |
      | ==> Notification                          |
      | ✓ fixed and verified                      |
    And the process exit code is 0
    And no HTTP calls to telegram.org or any public host occurred
    And no "docker" subprocess was spawned

  Scenario: Operator-confirmed finding waits for Enter
    Given "make demo" is running
    When the pipeline reaches the Tier 1 finding
    Then the terminal prompts "Press Enter to simulate [Fix now] tap"
    And the pipeline blocks until Enter is pressed
    And after Enter the fix path proceeds and the notification is rendered

  Scenario: Mocked actions are explicitly labelled
    Given the demo has finished
    When the operator reads the output
    Then every externally-impactful line is prefixed "[mock]"
      | mocked action           |
      | [mock] docker pull      |
      | [mock] docker tag       |
      | [mock] telegram send    |

  Scenario: Integration test encodes the demo path
    Given pytest is invoked
    When "pytest poc/tests/integration/test_BC_001_mocked_e2e.py" runs
    Then it passes
    And it covers: scan → hub ingest → urgency decision → fix dispatch →
        notification render → verify re-scan
    And it does not require stdin (the test uses a non-interactive variant that
        skips the Enter prompt)

  Scenario: Unit helpers have focused tests
    Given pytest is invoked
    When the unit tier runs
    Then make_id, extract_cvss, format_notification, markdown escape, and the
        executor dispatcher each have at least one test
    And coverage of poc/hub/* is at least 60%

  Scenario: Smoke mode (CI gate) delegates to the demo
    When "bash poc/demo.sh --smoke" runs
    Then it invokes "python -m poc.demo --non-interactive"
    And exits 0
    And requires no external services
```

## Open questions

- (none — resolved during 2026-10-29 clarification + 3 virtual-user iterations)

## Links

- Design: `DESIGN.md § Pipeline`, `memory/design-decisions.md § Urgency tiers / Auto-fix trust model`
- Related: BC-000 (scaffold — prerequisite), BC-002+ (future: dependency-injection refactor to replace the env-flag mocking)
