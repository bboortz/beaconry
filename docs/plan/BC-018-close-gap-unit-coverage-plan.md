# BC-018 close-gap — Unit coverage for telegram-mock + hub env dials

*Branch: `fix/BC-018-close-gap-unit-coverage`*
*Start date: 2026-10-30*
*Parent: `docs/plan/BC-018-telegram-mock-plan.md`*

---

## Situation

Post-ship three-lens audit found the BC-018 shipping-evidence table marked 5 rows ✓ pointing at unit tests that did not exist:
- `poc/tests/unit/test_telegram_mock.py` (whole file absent — 3 rows claimed it)
- `test_hub_helpers.py::test_telegram_base_url_env_overrides_default` (absent)
- `test_hub_helpers.py::test_skip_grype_db_env_only` (absent)

The e2e test (`test_BC_011_probe_flow.py`) exercises all five behaviors end-to-end, so BC-018 did ship functional. But the pyramid has no unit coverage — a mock-only regression (e.g. telegram-mock's offset trim, body parser, or hub's base_url env parse) would only be caught by the slow e2e stack, not by `pytest --no-cov` fast feedback.

Scenario 7 of the BC-018 story ("dev-without-Telegram dividend — `make demo` works with `BEACONRY_TELEGRAM_BASE_URL`") also had no evidence row.

---

## Problems to solve

- No unit coverage for telegram-mock Bot API shim + driver endpoints
- No unit coverage for hub's `BEACONRY_TELEGRAM_BASE_URL` + `BEACONRY_SKIP_GRYPE_DB` env dials
- Shipping-evidence table in BC-018 plan contains false ✓ claims pointing at nonexistent files

---

## Approach

1. Add `poc/telegram_mock/__init__.py` so test import `from poc.telegram_mock import mock_bot` resolves.
2. Add dual-path `beaconry_health` import to `mock_bot.py` matching hub.py / scout.py pattern (container keeps bare import; host test uses `poc.beaconry_health`).
3. Write `poc/tests/unit/test_telegram_mock.py` — FastAPI TestClient-driven unit tests for the 5 Bot API endpoints, driver endpoints, getUpdates offset trim, reset, four-endpoint health router, permissive body parser.
4. Append 4 tests to `test_hub_helpers.py` using `importlib.reload(hub)` under `monkeypatch.setenv` to verify module-level env-parsed globals: `TELEGRAM_BASE_URL`, `SKIP_GRYPE_DB` (set-only), `MOCK` back-compat alias, default-empty.
5. Add CAP-T055 → `test_telegram_mock.py` and CAP-T056 → `test_hub_helpers.py` to CAPABILITIES.md verified-by lists; add CAP-T056 to the Covers block in `test_hub_helpers.py`.

No behavior change in hub.py or mock_bot.py beyond the import fallback — purely additive.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| telegram-mock implements /getMe /getUpdates /sendMessage /editMessageText /answerCallbackQuery /deleteWebhook | unit test | `test_telegram_mock.py::test_bot_api_subset_present` | ✓ |
| /__driver__/inject-callback → getUpdates delivery | unit test | `test_telegram_mock.py::test_injected_callback_delivered_via_getupdates` | ✓ |
| getUpdates offset trims acked updates (BC-018 regression lesson) | unit test | `test_telegram_mock.py::test_getupdates_offset_trims_acked` | ✓ |
| /__driver__/sent captures sendMessage + editMessageText + answerCallbackQuery | unit test | `test_telegram_mock.py::test_sent_captures_outbound` | ✓ |
| /__driver__/reset clears all state | unit test | `test_telegram_mock.py::test_reset_clears_all_state` | ✓ |
| mock_bot reuses `beaconry_health.build_router` (four endpoints) | unit test | `test_telegram_mock.py::test_health_router_four_endpoints_present` | ✓ |
| Permissive body parser handles form-encoded + JSON | unit test | `test_telegram_mock.py::test_permissive_body_parser_handles_form_encoded` | ✓ |
| Hub `BEACONRY_TELEGRAM_BASE_URL` env sets `TELEGRAM_BASE_URL` module global | unit test | `test_hub_helpers.py::test_telegram_base_url_env_overrides_default` | ✓ |
| Unset env → empty string default | unit test | `test_hub_helpers.py::test_telegram_base_url_defaults_to_empty_string` | ✓ |
| `BEACONRY_SKIP_GRYPE_DB=1` alone sets `SKIP_GRYPE_DB` without implying MOCK | unit test | `test_hub_helpers.py::test_skip_grype_db_env_only` | ✓ |
| `BEACONRY_MOCK=1` back-compat alias still implies `SKIP_GRYPE_DB` | unit test | `test_hub_helpers.py::test_mock_env_still_implies_skip_grype_db` | ✓ |
| CAPABILITIES bidirectional traceability | verify-traceability | `make check` | ✓ |
| Scenario 7 dividend — hub routes to a configurable Telegram base URL instead of api.telegram.org | e2e stack | `make test-e2e` (12 passed) + unit test `test_telegram_base_url_env_overrides_default` | ✓ |
| Scenario 7 dividend — literal `make demo` + host-run telegram-mock recipe | live manual | ⚠️ **DEFERRED** — needs `BEACONRY_TELEGRAM_BASE_URL` added to `docker-compose.yml` env list + `extra_hosts: host.docker.internal:host-gateway` on the hub service for container→host networking. Tracked in `docs/plan/IDEAS.md` as BC-019 candidate. | ⚠️ |
| `make check` fast tier green | multi-gate | run after Phase 3 | ✓ |

---

## Scenario 7 live-verification — DEFERRED

Attempted on 2026-10-30 with:
```bash
docker compose -p beaconry-test -f poc/docker-compose.test.yml up -d telegram-mock
BEACONRY_TELEGRAM_BASE_URL=http://localhost:9080 \
  BEACONRY_SKIP_GRYPE_DB=1 TELEGRAM_BOT_TOKEN=test-token-1234567890 \
  make demo
```

Result: hub container crashed with `InvalidToken` from the real `api.telegram.org`. Root cause: two gaps that are out of scope for a "close-gap unit coverage" task:

1. `poc/docker-compose.yml` does not propagate `BEACONRY_TELEGRAM_BASE_URL` or `BEACONRY_SKIP_GRYPE_DB` to the hub container (allowlisted in `verify_compose_env_propagation.py` as test-only).
2. Even propagated, `localhost:9080` inside the hub container does not resolve to the host's telegram-mock. Needs `extra_hosts: host.docker.internal:host-gateway` on the hub service.

The behavioral promise of CAP-T056 (hub routes to a configurable base URL) IS verified — exhaustively via `make test-e2e` + the new unit tests. The dev-ergonomics *literal* recipe is a BC-019 candidate. Added to `docs/plan/IDEAS.md`.

---

## Merge plan

1. Branch from `dev` → `fix/BC-018-close-gap-unit-coverage`
2. Add plan doc (this file) + new unit tests + CAPABILITIES edits
3. `make check` green → commit → merge to `dev`

No design delta; no DESIGN.md update; no CAP additions (only verified-by entries).
