---
id: BC-018
title: telegram-mock service for test-what-you-ship e2e (supersedes BC-017's test-endpoint approach)
status: shipped
created: 2026-10-30
branch: feat/BC-018-telegram-mock
plan: docs/plan/BC-018-telegram-mock-plan.md
---

# BC-018: telegram-mock service

## Narrative

BC-017 shipped a working e2e harness by baking `/__test__/*` endpoints into hub.py behind an env guard. Reviewed in the audit: the production `beaconry-hub:latest` image ships ~70 LOC of test machinery as dead code, operators who set `BEACONRY_TEST_MODE=1` by mistake expose insert-DB + handler-invoke + wipe-DB endpoints to anyone with HTTP access. The guard is strong but the surface area and misconfig blast radius are both substantial. BC-018 replaces the shared-codebase approach with a separate `telegram-mock` service: a mini Telegram Bot API shim that hub's real `python-telegram-bot` client talks to instead of `api.telegram.org`. Hub.py becomes 100% identical between prod and e2e — no env guards, no test endpoints, no `BEACONRY_TEST_MODE`. Test driver injects fake user taps into telegram-mock; mock delivers them via the real getUpdates protocol; hub runs its real production handler path; hub's responses go back through mock which captures them for assertions.

## Trigger

Architectural audit after BC-017 shipped: shipped surface area smell + misconfig risk. User framed as "sources AND containers should be separated — telegram-mock as its own service".

## Urgency

N/A — architectural hardening of the test harness; no operator feature.

## Expected outcome

- New service `poc/telegram-mock/` implementing the Telegram Bot API endpoints python-telegram-bot calls:
  - `POST/GET /bot<token>/getUpdates` (long-polling)
  - `POST /bot<token>/sendMessage`
  - `POST /bot<token>/editMessageText`
  - `POST /bot<token>/answerCallbackQuery`
  - `POST /bot<token>/getMe`
- Plus test-driver endpoints:
  - `POST /__driver__/inject-callback` — queue a callback_query update
  - `GET /__driver__/sent` — captured sendMessage/editMessageText log
  - `POST /__driver__/reset` — clear queue + captured messages
- Hub.py:
  - **Delete** the `/__test__/*` block + `BEACONRY_TEST_MODE` env var (introduced in BC-017)
  - **Add** `BEACONRY_TELEGRAM_BASE_URL` env var; when set, `Application.builder().base_url(...)` points at it
  - **Split** `BEACONRY_MOCK` logic: skip grype DB download when `BEACONRY_SKIP_GRYPE_DB=1`; skip Telegram wiring when `TELEGRAM_BOT_TOKEN` is empty. `BEACONRY_MOCK` stays as a compatibility alias (sets both internally)
- New `poc/docker-compose.test.yml` orchestrates 3 services: `beaconry-hub-test` + `telegram-mock` + `probe-target-test`. Hub-test configured with `BEACONRY_TELEGRAM_BASE_URL=http://telegram-mock:8080`
- Updated `poc/tests/e2e/test_BC_011_probe_flow.py` drives via telegram-mock's `/__driver__/*` endpoints + `docker exec` for DB seeding
- `make test-e2e` orchestrates up→wait→test→down (unchanged interface)
- Prod image `beaconry-hub:latest` contains zero test code (`docker save beaconry-hub:latest | tar xO ... | grep "__test__"` returns no hits)
- Zero misconfig risk: no env var can toggle test endpoints into production
- CAPs: deprecate CAP-T054 (test-mode guard); new CAP-T055 (telegram-mock service), CAP-T056 (hub Telegram base_url configurability)
- Side benefit: operator can run `make demo` against telegram-mock for local dev without creating a real Telegram bot token

## Out of scope

- Full Telegram Bot API coverage — only the ~5 endpoints `python-telegram-bot` actually calls
- Webhook mode (we use long-polling only)
- Chat / user management beyond the minimum fields python-telegram-bot expects
- Historical message storage / search
- Testing against the real `api.telegram.org` — still available via `make demo` in real mode
- Dashboard for inspecting mock state (driver endpoints suffice)
- Multi-tenancy on the mock (one logical bot per instance)

## Acceptance criteria (Gherkin)

```gherkin
Feature: telegram-mock service enables e2e tests without shared source

  Background:
    Given poc/docker-compose.test.yml is up under project name `beaconry-test`
    And `beaconry-hub-test`, `telegram-mock`, `probe-target-test` are running
    And hub-test's TELEGRAM base_url points at `http://telegram-mock:8080`

  Scenario: hub.py source has no test machinery
    When grep "__test__" is run against poc/hub/hub.py
    Then it returns no hits
    And grep "BEACONRY_TEST_MODE" returns no hits
    And the shipped beaconry-hub:latest image is byte-identical between prod and test contexts

  Scenario: telegram-mock implements the subset python-telegram-bot calls
    When python-telegram-bot startup runs getMe
    Then telegram-mock returns a valid bot-info payload (id, is_bot, first_name, username)
    And the hub Application boots clean

  Scenario: injected callback_query reaches hub's handler
    Given a finding seeded in hub-test's DB via docker exec
    When the test driver POSTs to /__driver__/inject-callback with data="verify:<id>"
    Then the next getUpdates call returns the queued update
    And python-telegram-bot's CallbackQueryHandler dispatches to on_button_click
    And hub.on_button_click runs the real production code path (no mocks inside hub)

  Scenario: hub's outbound sendMessage / editMessageText are captured
    Given hub emits a terminal "Service healthy" message
    When the test driver GETs /__driver__/sent
    Then the captured list contains an entry for the editMessageText call
    And the captured text contains "Service healthy"

  Scenario: all 4 BC-011 terminal states round-trip through telegram-mock
    When the test invokes each of the four scenarios
    Then each terminal message is captured correctly in /__driver__/sent
    And the DB status transitions match BC-011's contract

  Scenario: BEACONRY_TEST_MODE removal doesn't break any gate
    When make check runs post-BC-018 merge
    Then all drift guards + lint rules pass
    And memory_drift_bans.toml includes a ban for pre-BC-018 claims that /__test__/* endpoints exist

  Scenario: dev-without-Telegram dividend
    Given an operator sets BEACONRY_TELEGRAM_BASE_URL=http://localhost:8080 and runs telegram-mock locally
    When they `make demo`
    Then the full hub + scout pipeline works without a real Telegram bot token
    And demo CVE notifications are visible via /__driver__/sent
```

## Capabilities delivered

- **CAP-T055: telegram-mock service — mini Telegram Bot API shim + test driver endpoints** (technical, active) — new. Separate `poc/telegram-mock/` service built from its own Dockerfile. Implements the 5 Telegram endpoints python-telegram-bot calls (getUpdates/sendMessage/editMessageText/answerCallbackQuery/getMe) + three `/__driver__/*` endpoints (inject-callback/sent/reset) the e2e test suite uses.
- **CAP-T056: Hub configurable Telegram API base URL via `BEACONRY_TELEGRAM_BASE_URL`** (technical, active) — new. When set, hub's `Application.builder().base_url(...)` points at it. Enables e2e against telegram-mock and dev-without-real-bot-token local workflows.

Deprecates: **CAP-T054** (test-mode guard with /__test__/* endpoints in hub.py). Marked `deprecated` with pointer to CAP-T055. Related code deleted. Tests migrated.

## Open questions

- (none — resolved by the "sources and containers separated" architectural choice)

## Links

- Supersedes: BC-017's test-endpoint-in-hub approach
- Related: BC-011 (handler under test), BC-013 (compose-aware status CLI uses a similar isolation pattern)
- Side benefit tracked: operator-facing dev-without-Telegram via BEACONRY_TELEGRAM_BASE_URL
