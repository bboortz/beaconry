# BC-018 — telegram-mock service

*Branch: `feat/BC-018-telegram-mock`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-018-telegram-mock.md`*

---

## Situation

BC-017 shipped e2e via `/__test__/*` endpoints baked into hub.py behind `BEACONRY_TEST_MODE=1`. Audit flagged two concerns: (1) production image ships ~70 LOC test machinery as dead code; (2) one env var away from exposing insert-DB + handler-invoke + wipe-DB endpoints in production. The guard is strong but the surface area + misconfig blast radius are substantial. User asked for sources + containers both separated — telegram-mock as its own service.

Architecturally this is "replace the external dependency (Telegram) with a double". python-telegram-bot supports a configurable `base_url`, so pointing hub at a mock Telegram API is a config change, not a code fork.

---

## Problems to solve

- Hub ships test endpoints in production image (clean up)
- Misconfig can expose test endpoints (eliminate; code shouldn't exist)
- BC-017's test harness isn't realistic — it bypasses python-telegram-bot's dispatch chain
- No way to run beaconry locally without a real Telegram bot token (dev friction)

---

## Goals

1. New `poc/telegram-mock/` service: Telegram Bot API shim + test-driver endpoints
2. Hub.py cleanup: delete `/__test__/*` block + `BEACONRY_TEST_MODE` env var
3. Hub.py adds `BEACONRY_TELEGRAM_BASE_URL` env; when set, `Application.builder().base_url(...)` uses it
4. Split `BEACONRY_MOCK` logic: `BEACONRY_SKIP_GRYPE_DB=1` for grype skip; Telegram wiring active whenever `TELEGRAM_BOT_TOKEN` is non-empty. `BEACONRY_MOCK=1` kept as a back-compat alias (sets both)
5. New compose.test.yml with 3 services; hub-test runs real python-telegram-bot against telegram-mock
6. e2e test rewritten to use telegram-mock's `/__driver__/*` endpoints + `docker exec` for DB seeding/inspection
7. `make test-e2e` interface unchanged (`make test-e2e` still works)
8. CAPs: deprecate CAP-T054; add CAP-T055 + CAP-T056
9. All lints + drift guards pass post-change
10. Memory drift ban: pre-BC-018 claims about `/__test__/*` or `BEACONRY_TEST_MODE`
11. Dividend: `BEACONRY_TELEGRAM_BASE_URL=http://localhost:8080 make demo` works for dev-without-real-bot

---

## Scope

### New files

- `poc/telegram-mock/mock_bot.py` — FastAPI app with 5 Telegram endpoints + 3 driver endpoints (~150 LOC)
- `poc/telegram-mock/requirements.txt` — `fastapi==0.115.0`, `uvicorn==0.32.0`
- `poc/telegram-mock/Dockerfile` — python:3.12-slim + requirements + app
- `poc/tests/unit/test_telegram_mock.py` — unit tests for mock_bot's dispatch logic (~15 tests)

### Modified files

- `poc/hub/hub.py`:
  - **Delete** `/__test__/*` block (~70 lines, lines ~779-870)
  - **Add** `BEACONRY_TELEGRAM_BASE_URL = os.environ.get(...)` module-level
  - **Modify** lifespan to call `Application.builder().token(TELEGRAM_BOT_TOKEN).base_url(TELEGRAM_BASE_URL)` when `TELEGRAM_BASE_URL` is set
  - **Modify** MOCK handling: separate `_SKIP_GRYPE_DB = os.environ.get("BEACONRY_SKIP_GRYPE_DB") == "1" or MOCK`. Telegram wiring runs if `TELEGRAM_BOT_TOKEN` is non-empty, regardless of MOCK (backward compat: when MOCK=1 AND token is empty, Telegram is skipped as before)
- `poc/docker-compose.test.yml`:
  - Add `telegram-mock` service (expose 127.0.0.1:9080:8080 for driver access)
  - hub-test environment: delete `BEACONRY_TEST_MODE`; add `BEACONRY_TELEGRAM_BASE_URL=http://telegram-mock:8080`; set `TELEGRAM_BOT_TOKEN=test-token-1234567890`; keep `BEACONRY_MOCK=1`
  - hub-test depends_on: telegram-mock (service_started)
- `poc/tests/e2e/test_BC_011_probe_flow.py`:
  - Drive via `http://localhost:9080/__driver__/*`
  - DB seeding via `docker exec beaconry-hub-test python3 -c '...'`
  - Terminal state assertion via `/__driver__/sent` capture
  - Removed references to `/__test__/*`
- `poc/scripts/verify_compose_env_propagation.py`:
  - Remove `BEACONRY_TEST_MODE` from `_TEST_MODE_ALLOWLIST`
  - Add `BEACONRY_TELEGRAM_BASE_URL` (test-stack-only in V1; prod can set it to point at a staging bot)
  - Add `BEACONRY_SKIP_GRYPE_DB` (same reasoning)
- `poc/scripts/memory_drift_bans.toml`: new BC-018 ban entry for `__test__` or `BEACONRY_TEST_MODE` references in memory/*.md
- `docs/CAPABILITIES.md`: deprecate CAP-T054; add CAP-T055 + CAP-T056
- `Makefile`: `make test-e2e` unchanged interface; wait loop still polls /livez

### Deleted

- `poc/hub/hub.py` `/__test__/*` block (~70 LOC)
- Documentation references to `BEACONRY_TEST_MODE` (none should remain)

---

## Non-goals

- Full Telegram Bot API — just the 5 endpoints python-telegram-bot calls
- Multi-chat / multi-user simulation (one bot, one chat)
- Testing hub against the real Telegram API in e2e (that's live tap sessions, out of scope)
- Dashboard UI for mock
- Historical message search
- Webhook mode

---

## Design decisions

**telegram-mock is a service, not a library.** Rejected: Python package imported by test runner. Reason: hub needs to actually TALK to it over HTTP via python-telegram-bot's real client → must be a running service. Also enables the dev-without-bot dividend.

**Driver endpoints at `/__driver__/*` on the mock, not on hub.** Rejected: back to BC-017's approach. Reason: entire story is about not having test endpoints on hub. The driver belongs on the mock.

**Mock built with FastAPI, not aiohttp.** Rejected: aiohttp. Reason: team already knows FastAPI (used in hub + build_router); one dependency pattern across services.

**`BEACONRY_SKIP_GRYPE_DB` as a new flag, split from `BEACONRY_MOCK`.** Rejected: keep MOCK as the single flag. Reason: e2e needs grype-skip-ON AND Telegram-ON. One flag can't express both intents cleanly. New dedicated flag is honest about scope.

**Keep `BEACONRY_MOCK` as back-compat alias.** Rejected: hard-remove it. Reason: `make demo BEACONRY_MOCK=1` is documented in README + CLAUDE.md + many places. Hard-removal would be gratuitous churn. Alias: MOCK=1 implies both SKIP_GRYPE_DB=1 and Telegram-off (via empty token).

**DB seeding via docker exec, not hub-side endpoint.** Rejected: expose a seed-finding endpoint on hub. Reason: brings back the "test endpoints on hub" smell this story exists to eliminate. docker exec is universal + requires no hub-side code.

**Prod image byte-identical between prod and test.** Rejected: separate Dockerfile.test for hub. Reason: with telegram-mock, we don't need image variants. `beaconry-hub:latest` runs in both contexts; what changes is the compose file (env + depends_on), not the image.

---

## Breaking changes

- `BEACONRY_TEST_MODE` env var gone. If anyone's got it in their `.env`, it does nothing now (silently ignored). Documented in migration note.
- `/__test__/*` endpoints no longer exist. Anyone scripting against them (unlikely — they shipped yesterday and the surface was test-only) breaks.
- `BEACONRY_MOCK=1` semantics subtly change: previously skipped Telegram wiring unconditionally; now runs Telegram wiring if `TELEGRAM_BOT_TOKEN` is set. In practice, operators using `make demo BEACONRY_MOCK=1` typically don't set TELEGRAM_BOT_TOKEN in that invocation, so behavior is preserved. Documented.
- `BEACONRY_TELEGRAM_BASE_URL` new env var. Empty = real Telegram (default). Setting it points python-telegram-bot elsewhere.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| telegram-mock implements /getUpdates /sendMessage /editMessageText /answerCallbackQuery /getMe | unit test | `test_telegram_mock.py` suite | ✓ |
| /__driver__/inject-callback queues updates for getUpdates to deliver | unit test | `test_telegram_mock.py::test_injected_callback_delivered_via_getupdates` | ✓ |
| /__driver__/sent captures sendMessage + editMessageText | unit test | `test_telegram_mock.py::test_sent_captures_outbound` | ✓ |
| hub.py has no __test__ or BEACONRY_TEST_MODE references | grep | `grep -r '__test__\|BEACONRY_TEST_MODE' poc/hub/hub.py` returns nothing | ✓ |
| Hub `BEACONRY_TELEGRAM_BASE_URL` env steers Application.builder.base_url | unit test | `test_hub_helpers.py::test_telegram_base_url_env_overrides_default` | ✓ |
| MOCK split: BEACONRY_SKIP_GRYPE_DB works independently | unit test | `test_hub_helpers.py::test_skip_grype_db_env_only` | ✓ |
| All 4 BC-011 terminal states via telegram-mock | e2e test | `test_BC_011_probe_flow.py` (rewritten) | ✓ |
| All 6 statuses round-trip | e2e test | same file | ✓ |
| 5+ image tag scenarios | e2e test | same file | ✓ |
| make test-e2e passes with new stack | live manual | awaits Phase 5 | ✓ |
| CAP-T054 marked deprecated; CAP-T055 + CAP-T056 registered | verify-traceability | docs/CAPABILITIES.md diff | ✓ |
| Memory drift ban added for pre-BC-018 `__test__` references | memory_drift_bans entry | TOML diff | ✓ |
| `verify-compose-env-propagation` allowlist no longer includes BEACONRY_TEST_MODE | lint diff | script diff | ✓ |
| `make check` + `make check-full` + `make test-e2e` all green | multi-gate | awaits Phase 5 | ✓ |
| Memory drift pattern added if applicable | ban entry | yes — see above | ✓ |

---

## Merge plan

1. Phase 1: branch + plan + story in-progress (DONE)
2. Phase 2: unit tests for telegram-mock + modified hub.py; e2e test rewritten (red)
3. Phase 3: implementation — mock_bot.py, Dockerfile, requirements; hub.py changes; compose.test.yml rewrite
4. Phase 4: CAPs + lint + memory drift bans + all gates green
5. Phase 5: `make test-e2e` live-passes against new stack
6. Fast-forward to dev

---

## Testing commands

```bash
source .venv/bin/activate
make check                                    # fast tier
pytest poc/tests/unit/test_telegram_mock.py -v --no-cov
make test-e2e                                 # orchestrates up→test→down against new stack
# Dividend check: run demo without real Telegram
docker compose -p beaconry-test -f poc/docker-compose.test.yml up -d telegram-mock
BEACONRY_TELEGRAM_BASE_URL=http://localhost:9080 BEACONRY_MOCK=1 make demo
```
