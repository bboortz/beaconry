# BC-124 — Heartbeat retry + HUB_HTTP_PORT + BC-117/119 integration tests

*Branch: `feat/BC-124-heartbeat-retry-port-env-integration-tests`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-124-heartbeat-retry-port-env-integration-tests.md`*

---

## Situation

Three items split off from BC-123 to keep the diff reviewable:

1. Scout `/heartbeat` has no retry. BC-107 added `/scan` persistent
   retry via `pending_scans`; heartbeat path stayed 1-shot. Transient
   network blips drop keepalives.
2. Hub port `8000` is hardcoded in the `__main__` block of hub.py.
   Scout has `SCOUT_HTTP_PORT`; hub doesn't.
3. BC-117 (cross-repo) and BC-119 (gzip) shipped unit-only. BC-120
   + BC-122 have integration tests. Backfill.

## Goals

1. `_heartbeat_with_retry` wraps `heartbeat_once` with capped
   exponential backoff. (Scenario 1)
2. `HUB_HTTP_PORT` env in hub `__main__`. (Scenario 2)
3. `poc/tests/integration/test_BC_117_cross_repo_cve_fix_workflow.py` —
   end-to-end TestClient flow exercising the awaiting_upstream path.
   (Scenario 3)
4. `poc/tests/integration/test_BC_119_scout_scan_gzip_compression.py` —
   end-to-end TestClient flow sending a real gzipped body. (Scenario 4)

## Scope

- `poc/scout/scout.py` — `_heartbeat_with_retry` helper; call it from
  `_heartbeat_loop_forever` instead of `heartbeat_once` directly. New
  env vars `BEACONRY_HEARTBEAT_RETRY_MAX_ATTEMPTS` (3),
  `BEACONRY_HEARTBEAT_RETRY_BASE_S` (2).
- `poc/hub/hub.py` — `HUB_HTTP_PORT = int(os.environ.get(..., "8000"))`;
  `__main__` reads it. Also in docker-compose.yml + .env.example
  + VARIABLES.md.
- `poc/tests/integration/test_BC_117_cross_repo_cve_fix_workflow.py` —
  new. 3-4 scenarios exercising the happy path + awaiting_upstream
  state transition + policy-mapped Telegram hint.
- `poc/tests/integration/test_BC_119_scout_scan_gzip_compression.py` —
  new. 2-3 scenarios: large gzipped body round-trip, zip-bomb
  decompression cap, legacy identity-body still works.
- `poc/tests/unit/test_BC_124_heartbeat_retry_and_port.py` — unit
  tests for the retry helper + port env parsing.
- `docs/CAPABILITIES.md` — CAP-T226 + CAP-T227.

## Non-goals

- Heartbeat persistent queue (payload is 200B; loss on restart is fine).
- Full forge/compose rewire of HUB_HTTP_PORT — image stays on 8000
  internally, env knob is for uvicorn.run only (local dev).
- Rewriting BC-117 or BC-119 — tests only.

## Design decisions

- **Heartbeat retry is in-memory** (vs BC-107's SQLite queue). The
  payload is tiny + stateless (host+version+health). A persistent
  queue would be overkill. Transient blips are what retry solves;
  prolonged outages are what the next tick solves.
- **Retry cadence matches BC-112** (3 attempts, 2s base) — operators
  only need to remember one retry pattern.
- **HUB_HTTP_PORT default 8000** — pure backward compat.
- **Integration tests focus on wiring, not coverage** — unit tests
  already cover BC-117/119 atoms; the integration tests catch
  "did I correctly thread X through Y → Z" regressions.

## Breaking changes

### 1. API / return shape
None.

### 2. Data model
None.

### 3. Behavioral semantics

- Heartbeat failure retries up to 3× with 2s + 4s + 8s backoff.
  Previously 1-shot. Total worst-case wait: ~14s before next tick
  arrives. Doesn't exceed `SCOUT_HEARTBEAT_INTERVAL_S=60` default.
- `HUB_HTTP_PORT` unset = 8000 = pre-BC-124. Only operators who
  explicitly set the env see any difference.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1: heartbeat retries on transient failure | unit | `test_BC_124_heartbeat_retry_and_port.py::test_heartbeat_retries_on_failure` | ✓ |
| Scenario 2: HUB_HTTP_PORT env respected | unit | `test_BC_124_heartbeat_retry_and_port.py::test_hub_http_port_main_block_reads_env_default_8000` + `test_hub_http_port_env_parsing` | ✓ |
| Scenario 3: BC-117 integration test exists + passes | integration | `test_BC_117_cross_repo_cve_fix_workflow.py::test_awaiting_upstream_finding_persists_with_hint` + 3 sibling tests | ✓ |
| Scenario 4: BC-119 integration test exists + passes | integration | `test_BC_119_scout_scan_gzip_compression.py::test_gzip_body_round_trip` + 3 sibling tests | ✓ |
| Goal: retry backs off exponentially | unit | `test_heartbeat_retry_backoff_exponential` | ✓ |
| Goal: retry gives up after MAX_ATTEMPTS | unit | `test_heartbeat_retry_abandons_after_max_attempts` | ✓ |
| Goal: heartbeat success on first attempt skips sleep | unit | `test_heartbeat_succeeds_on_first_attempt` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | pure additive | ✓ |

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_124_heartbeat_retry_and_port.py -v
pytest poc/tests/integration/test_BC_117_cross_repo_cve_fix_workflow.py poc/tests/integration/test_BC_119_scout_scan_gzip_compression.py -v
```
