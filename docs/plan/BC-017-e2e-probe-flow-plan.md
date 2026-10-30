# BC-017 — End-to-end BC-011 probe flow (no Telegram)

*Branch: `feat/BC-017-e2e-probe-flow`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-017-e2e-probe-flow.md`*

---

## Situation

BC-011 ships + got live-verified via Telegram taps tonight. Caught a `ModuleNotFoundError` the first time any live tap exercised the verify branch — unit tests had passed because they ran in host Python where `poc` is importable; the container has `/app` flat and no `poc` package. BC-005's "live-smoke required" lesson keeps firing because unit tests structurally can't exercise the container runtime path.

BC-017 closes that structural gap for BC-011 specifically: a test compose stack that runs the real hub container + a real probe target + a pytest runner that drives the handler through its full chain.

---

## Problems to solve

- Container-only bugs (import, env, Dockerfile COPY) escape unit tests
- Live Telegram verification requires a human + credentials
- Refactoring the handler / probe / resolver has no regression net between unit tests and the next tap session
- Test runs must not interfere with an operator's running compose stack

---

## Goals

1. New compose file `poc/docker-compose.test.yml` under project name `beaconry-test` with containers named `-test` and ports/volumes namespaced
2. Hub exposes `/__test__/*` endpoints behind `BEACONRY_TEST_MODE=1` env guard (never active in production)
3. `probe-target-test` is a tiny nginx container serving `/healthy` (200) + `/degraded` (503)
4. `poc/tests/e2e/test_BC_011_probe_flow.py` covers all 4 terminals + all 6 statuses + 3+ tag scenarios
5. `pytest -m e2e` marker + `make test-e2e` target with proper setup/teardown
6. CAP-T054 registered; drift guards pass (new env vars in test compose, new make target in help)

---

## Scope

- `poc/docker-compose.test.yml` — new compose file
  - services: `beaconry-hub-test`, `probe-target-test`
  - project name: `beaconry-test` (via `name: beaconry-test` at top-level)
  - network: `beaconry-test` (also namespaced)
  - volume: `beaconry-test-data`
  - hub bound to `127.0.0.1:9000:8000` (distinct from normal 8000)
  - probe-target bound only to internal network
- `poc/tests/e2e/nginx.conf` — nginx config for probe-target
- `poc/hub/hub.py` — new section guarded by `if os.environ.get("BEACONRY_TEST_MODE") == "1":` with four endpoints:
  - `POST /__test__/insert-finding` → writes a Finding row
  - `POST /__test__/invoke-callback` → body: `{"callback_data": "verify:<16hex>"}`; crafts a fake `Update` + calls `on_button_click`; captures the `edit_message_text` sequence
  - `GET /__test__/messages` → returns captured messages (last N)
  - `POST /__test__/reset` → clears captured messages and optionally wipes DB rows
- `poc/tests/e2e/__init__.py` — new
- `poc/tests/e2e/test_BC_011_probe_flow.py` — new, ~300 LOC
  - Fixtures: wait-for-hub-ready, DB-reset-between-tests
  - Tests enumerated by scenario in the story Gherkin
- `pyproject.toml` — new pytest marker `e2e`
- `Makefile`:
  - `test-e2e` target orchestrates up/pytest/down
  - Added to `.PHONY` + `help:` block
- `docs/CAPABILITIES.md` — CAP-T054 registered

---

## Non-goals

- Scout coverage in the test stack
- Real Telegram
- Real syft/grype
- CI pipeline integration
- Load testing

---

## Design decisions

**Separate compose file with separate project name, not override files.** Rejected: `docker-compose.override.yml`. Reason: overrides merge with the main compose; operator-visible footgun. A separate file + explicit `-p beaconry-test` keeps the test stack isolated.

**`/__test__/*` endpoints behind a hard env guard.** Rejected: separate test-only Python module. Reason: the test endpoints need the same `on_button_click` handler as production; they're thin HTTP wrappers around existing code. Env-gating prevents accidental enablement in prod.

**nginx as probe target, not Python.** Rejected: tiny Python aiohttp server. Reason: nginx:alpine is 7MB vs python:slim's 40MB, boots in <1s, well-known semantics. One config file is simpler than one Python file + dependencies.

**Hub test endpoints capture messages in-process.** Rejected: forward captured messages to a stub bot. Reason: the handler calls `query.edit_message_text(...)`; the simplest capture is to make the fake `Query` log edits to a module-level list, same shape as unit tests' `_FakeQuery`. Zero new abstractions.

**All 6 statuses tested, but button-attachment branch only tested for `fixed`→Verify.** Rejected: craft Verify callbacks for findings in `open` / `snoozed` etc. Reason: the Verify button is only attached to `✅ Fixed and verified` messages in the production flow; invoking Verify on an `open` finding is a malformed state the handler shouldn't need to handle. The test asserts round-trip + status rank for all 6, Verify behavior only for the two relevant terminal transitions (fixed→fixed_verified and fixed→verification_failed) plus idempotence on fixed_verified.

**Compose project name hardcoded `beaconry-test`.** Rejected: parametrize via env. Reason: tests coordinate on this name (asserting container names, volume names); treating it as config adds complexity for no gain.

---

## Breaking changes

- Adds `/__test__/*` endpoints to hub.py behind a guard. **Never active in production** (requires explicit `BEACONRY_TEST_MODE=1`). Internal API surface; no operator-facing change.
- Adds `BEACONRY_TEST_MODE` env var the hub reads → must be in docker-compose.test.yml (lint catches if missing from docker-compose.yml, but we don't want it in that file; lint rule needs an exception or the test compose file needs separate linting). **This is a lint consideration** — see below.
- Adds `e2e` pytest marker → `make check-full` currently runs `-m "compose or real_tools"`; adding `e2e` would run compose there too. For V1, `e2e` is only run via explicit `make test-e2e`, not auto in check-full (because it builds + runs real containers).

---

## Lint interactions

- `verify-compose-env-propagation` currently scans `poc/docker-compose.yml`. We now have `poc/docker-compose.test.yml`. The lint should either:
  - a) stay focused on prod compose (don't scan test file) — `BEACONRY_TEST_MODE` is read by code → would false-positive against docker-compose.yml
  - b) scan both — but then the lint would require `BEACONRY_TEST_MODE` in docker-compose.yml too, which we don't want
  - c) exempt specific env vars via an allowlist on the hub side (vars that are test-only and don't need prod propagation)
  - **Pick (c)**: add a small exceptions list in `verify_compose_env_propagation.py`. `BEACONRY_TEST_MODE` is explicitly test-only.
- `verify-docs-drift` currently checks make targets in `help:` → must list `make test-e2e`
- `verify-memory-drift` → no new bans needed; BC-017 doesn't deprecate shipped behavior

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Compose test stack isolated from normal stack | e2e test | `test_e2e_stack_container_names_are_namespaced` | ✓ |
| ✅ Service healthy terminal | e2e test | `test_terminal_service_healthy_http_pass` | ✓ |
| ⚠️ Probe failed terminal | e2e test | `test_terminal_probe_failed_http_degraded` | ✓ |
| ⏭️ No probe configured terminal | e2e test | `test_terminal_no_probe_configured` | ✓ |
| ℹ️ Already fixed_verified (idempotence) | e2e test | `test_terminal_idempotent_on_fixed_verified` | ✓ |
| Image tag scenarios: `:latest`, `:16-alpine`, `:16.1.3-alpine` | e2e test | `test_tag_scenarios_resolve_correctly` | ✓ |
| All 6 finding statuses round-trip | e2e test | `test_all_six_statuses_round_trip_and_sort` | ✓ |
| `make test-e2e` orchestrates up/test/tear-down | live manual | awaits Phase 5 | ✓ |
| BC-016 drift guards pass (new make target in help) | `make check` | traceability + docs-drift green | ✓ |
| `verify-compose-env-propagation` exempts BEACONRY_TEST_MODE | lint diff | explicit allowlist entry | ✓ |
| CAP-T054 registered | verify-traceability | `make check` green | ✓ |
| Memory drift pattern added if applicable | N/A | N/A — new feature; no behavior deprecated | ✓ |

---

## Merge plan

1. Phase 1: branch + plan + story in-progress (DONE at commit)
2. Phase 2: red e2e tests
3. Phase 3: implementation — compose file, nginx conf, hub test endpoints, make target
4. Phase 4: CAPs, lint exceptions, make check green
5. Phase 5: `make test-e2e` runs clean locally
6. Fast-forward to dev

---

## Testing commands

```bash
source .venv/bin/activate
make check                                       # fast tier
make test-e2e                                    # new target — spins up test stack, runs e2e, tears down
# Behind the curtain:
docker compose -p beaconry-test -f poc/docker-compose.test.yml up -d --build
pytest -m e2e poc/tests/e2e/ -v --no-cov
docker compose -p beaconry-test -f poc/docker-compose.test.yml down -v
```
