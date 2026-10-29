---
id: BC-002
title: Real pipeline on one container (no auto-fix)
status: shipped
created: 2026-10-29
branch: feat/BC-002-real-pipeline-one-container
plan: docs/plan/BC-002-real-pipeline-one-container-plan.md
---

# BC-002: Real pipeline on one container (no auto-fix)

## Narrative

Same operator from BC-001 ran `make demo`, saw the mocked pipeline, nodded, and now wants to know "but does it REALLY work?" They have a Telegram bot ready, nginx:1.24 pulled locally, syft + grype installed, and ~3 minutes. They fill `poc/.env`, run `make demo` again — same command, same muscle memory — and this time the *real* pipeline runs. Real syft scans the real nginx:1.24, real grype matches real CVEs, their phone buzzes with a real Telegram message carrying real finding details. [Fix now] isn't wired yet — the button replies "⚙️ fix execution coming in BC-003" — but [Snooze 4h] and [Accept risk] work and update a dev SQLite DB. The mocked path is still reachable via `BEACONRY_MOCK=1 make demo` — the same env flag tests and `--smoke` already use.

## Trigger

Operator finished the BC-001 mock demo, has a Telegram bot ready, pulled nginx:1.24 locally, installed syft + grype, filled `poc/.env`. Types `make demo`.

## Expected outcome

- Single command `make demo` auto-dispatches:
  - `BEACONRY_MOCK=1` set → mock pipeline (unchanged from BC-001)
  - otherwise → real syft → real grype → real Telegram
- Within ~2 minutes (plus one-time grype DB download) the operator's phone shows a Telegram message with real CVE details, Tier label, three inline buttons
- [Snooze 4h] + [Accept risk] update finding status in `.beaconry-cache/dev.db`; subsequent `make demo` runs don't re-notify snoozed/accepted findings
- [Fix now] edits the message to "⚙️ fix execution coming in BC-003"
- `make demo ARGS=--self-test` sends canary "beaconry is alive" and exits — no scan
- Missing `TELEGRAM_BOT_TOKEN` → named error pointing to `poc/.env.example` AND the `BEACONRY_MOCK=1 make demo` fallback
- Grype DB cached under `.beaconry-cache/grype-db/` (gitignored), survives across runs
- Dev DB at `.beaconry-cache/dev.db` — doesn't conflict with the Compose POC's `/data/beaconry.db`

## Out of scope

- Docker Compose stack validation (separate story, deferred)
- Full scout service with Docker socket mount + polling loop
- Auto-fix execution path (BC-003)
- Verify-after-fix (BC-003)
- Multiple images, pacman packages, open-port scanning (BC-004+)
- Multi-host canary ring (V2)
- Health check waterfall (BC-003)
- CISA KEV feed integration — kev flag stays fixture-only; real findings have kev=False

## Acceptance criteria (Gherkin)

```gherkin
Feature: Real pipeline against one container, same `make demo` command

  Background:
    Given syft and grype are installed on $PATH
    And docker is installed
    And nginx:1.24 is pulled locally
    And poc/.env contains valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
    And BEACONRY_MOCK is unset

  Scenario: `make demo` runs the real pipeline end-to-end
    When the operator runs "make demo"
    Then within 180 seconds a Telegram message arrives
    And the message names at least one CVE present in nginx:1.24
    And the message includes CVSS, severity, host, tier, and three inline buttons
    And the terminal mirrors the notification content

  Scenario: Snooze works
    Given a Telegram notification has arrived
    When the operator taps [⏰ Snooze 4h]
    Then the message is edited to confirm the snooze
    And the finding's status in .beaconry-cache/dev.db is "snoozed"
    And a subsequent "make demo" run does not re-notify that finding

  Scenario: Fix-now is honest about not being implemented
    When the operator taps [🔧 Fix now]
    Then the message is edited to "⚙️ fix execution coming in BC-003"
    And no docker subprocess is spawned
    And the finding's status remains "open"

  Scenario: Self-test canary
    When `make demo ARGS=--self-test` is run
    Then a canary Telegram message is sent containing "beaconry is alive"
    And no scan is executed
    And exit code is 0

  Scenario: Grype DB is cached across runs
    Given .beaconry-cache/grype-db/ does not exist
    When "make demo" is run for the first time
    Then grype downloads its DB into .beaconry-cache/grype-db/
    When "make demo" is run a second time
    Then grype uses the cached DB without re-downloading

  Scenario: Clear error with fallback hint when Telegram is unconfigured
    When "make demo" runs with TELEGRAM_BOT_TOKEN unset
    Then stderr names the missing variable
    And points to poc/.env.example
    And suggests `BEACONRY_MOCK=1 make demo` as the no-Telegram fallback
    And exit code is non-zero

  Scenario: Mock path still works via env flag
    When `BEACONRY_MOCK=1 make demo` is run
    Then the mocked BC-001 pipeline runs
    And no Telegram message is sent
    And exit code is 0

  Scenario: Integration test exercises real tools (opt-in)
    When "pytest -m real_tools poc/tests/integration/test_BC_002_real_pipeline.py" is invoked
    Then it skips unless syft and grype are on $PATH
    And when run, it executes real syft + real grype on nginx:1.24, mocks only Telegram,
        and asserts findings are produced

  Scenario: Findings with adversarial descriptions still send
    Given grype returns a finding whose description contains markdown-sensitive
          characters ([ ] _ * ` \ and emojis)
    When run_real_pipeline_once processes it
    Then Telegram accepts the message (no BadRequest from the parser)
    And the finding is saved to the dev DB

  Scenario: One bad notification does not crash the pipeline
    Given hub.notify raises for one of three findings
    When run_real_pipeline_once processes all three
    Then the failing finding is logged with its CVE id
    And the other two findings are still saved + notified
    And the function returns the successfully-notified findings

  Scenario: Per-run cap limits findings sent to Telegram
    Given 20 unique CVEs match above MIN_CVSS
    And BEACONRY_MAX_FINDINGS_PER_RUN=5
    When run_real_pipeline_once runs
    Then exactly 5 individual Telegram messages are sent
    And one additional digest message summarises "15 additional finding(s) suppressed"
    And the 5 sent are the highest-priority by (tier, -cvss)

  Scenario: CVEs shared across packages are deduplicated
    Given grype returns the same CVE id for both openssl and libssl1.1
    When run_real_pipeline_once runs
    Then only one Telegram message is sent for that CVE
    And the finding retained is the one with the highest CVSS

  Scenario: Real mode defaults MIN_CVSS to 9.0
    Given no explicit MIN_CVSS env is set
    When "make demo" is run in real mode
    Then MIN_CVSS is 9.0 (demo doesn't flood on base-image CVSS-7-8 noise)
    And mock mode continues to use MIN_CVSS=0 so fixtures still trigger
```

## Open questions

- (none — resolved during 2026-10-29 clarification + 3 virtual-user iterations + unification-into-`make demo` directive)

## Links

- Design: `DESIGN.md § Pipeline`
- Related: BC-001 (demo entrypoint this extends), BC-003 (real fix execution — depends on this)
