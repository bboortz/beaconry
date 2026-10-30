# BC-013 — Scout live HTTP probes + unified `make status` table display

*Branch: `feat/BC-013-scout-live-probes`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-013-scout-live-probes.md`*

---

## Situation

BC-010 shipped file-sentinel health for scout (`scout.live/.ready/.started/.healthz.json`). BC-012 extracted the convention into `beaconry_health/`. Hub has HTTP endpoints; scout does not. `make status` has two code paths — HTTP for hub, `docker exec` for scout — and emits raw Python exception class names when a container is down (`URLError`, `no scout.healthz.json; container not up?`). Operator feedback: output is "technical and unclean"; wants Docker-compose-ps-shaped table.

Detection latency for scout failures is bounded by `SCOUT_LIVE_MAX_AGE_S` (default 300s). Within that window, a dead scout and a happy scout read identical to the host.

---

## Problems to solve

- Scout's health is a historical snapshot, not a live probe → up-to-5min false-positive "healthy" window
- `make status` has two code paths (HTTP vs docker exec) → every new container forces decisions about which path to extend
- `make status` output includes raw exception class names and shell quoting → hostile for daily operator use
- Format isn't scannable — multiple services' states on one line separated by `·` doesn't align visually when values vary

---

## Goals

1. Scout gains a FastAPI HTTP server on internal port 8001 via `beaconry_health.build_router`; readyz probes "Docker socket openable"; healthz summary includes `last_scan_ts`, `images_seen`, `bytes_posted`, `docker_socket`
2. Compose: scout exposes `127.0.0.1:8001:8001`; healthcheck targets `/readyz` HTTP (replacing sentinel-file test)
3. `make status` rewritten:
   - One HTTP code path for both services
   - Delete `_fetch_scout_body_via_docker`, `SCOUT_LIVE_MAX_AGE_S`, `SCOUT_DATA_DIR` constants
   - Query Docker for container state (running / stopped / absent) via `docker inspect`; fall through to `down` when container missing
   - Table output: `NAME  STATE  HEALTH  LAST SCAN  DETAILS`, column-aligned, no raw tracebacks
4. Three exit codes: 0 all-healthy, 1 any-degraded, 2 any-down
5. Scout keeps writing `/data/scout.healthz.json` for a transitional release (so BC-010 scout_sentinel tests still exercise write paths) OR drops them entirely with the test updates — pick one in Phase 2 (decision below)
6. CAP-B017 + CAP-T043/044/045 registered
7. All BC-010 + BC-012 tests preserved; new BC-013 tests cover table format + exit codes + down-state handling

---

## Scope

- `poc/scout/scout.py`:
  - FastAPI `app`; `api.include_router(build_router(...))` with scout-specific providers
  - Scan loop becomes `asyncio.create_task(scan_loop_forever())` kicked off from a FastAPI lifespan
  - `collect_and_post` wrapped in `asyncio.to_thread(...)` to avoid blocking the event loop (syft + requests are blocking)
  - `_last_scan_ts`, `_images_seen`, `_bytes_posted`, `_docker_socket_ok`, `_started` module-local state
  - Keep SentinelWriter write calls? **No** — delete scout's use; the class stays in `beaconry_health` as a public option
  - `uvicorn.run(app, host="0.0.0.0", port=8001)` at `__main__`
- `poc/scout/requirements.txt`: + `fastapi` + `uvicorn`
- `poc/scout/Dockerfile`: `EXPOSE 8001`; nothing else changes (COPY beaconry_health/ already in place)
- `poc/docker-compose.yml`:
  - `scout.ports`: `["127.0.0.1:8001:8001"]`
  - `scout.healthcheck.test`: `python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8001/readyz').status==200 else 1)"`
  - Drop file-sentinel test syntax
- `poc/scripts/status.py` — rewritten:
  - Delete `_fetch_scout_body_via_docker`, `SCOUT_DATA_DIR`, `SCOUT_CONTAINER`, `SCOUT_LIVE_MAX_AGE_S`
  - New dataclass `ServiceStatus(name, state, health, last_scan, details)`
  - `gather_service_status(name, url)` function — handles all URLError / timeout / connection-refused / JSON-decode errors by returning a non-raising ServiceStatus with human phrasing
  - `print_table(statuses)` — column-aligned, header row
  - `main()` returns 0 / 1 / 2
- `Makefile`:
  - `status` target: still `$(PYTHON) -m poc.scripts.status`; env var cleanup
- `poc/tests/unit/test_status_cli.py` — rewritten:
  - Each existing test rewritten to new table format expectations
  - New tests: all-down state, partial-up state, column alignment, no-exception-class-name in output
- `poc/tests/unit/test_scout_http_health.py` — new:
  - Scout's FastAPI app /livez /readyz /startupz /healthz via TestClient
  - `/livez` returns 200 under Docker socket OSError (trap guard)
  - `/readyz` reflects docker socket status
  - `/healthz` includes last_scan_ts, images_seen, bytes_posted
- `poc/tests/integration/test_BC_010_compose_health_chain.py` — update:
  - scout healthcheck now references `/readyz` not sentinel files
  - scout exposes port 8001
- `poc/tests/unit/test_scout_health.py` — delete or re-purpose: BC-010 scout sentinel tests no longer exercise production scout.py path. Options: (a) delete, lose BC-010 CAP-T036 verification; (b) keep as SentinelWriter class smoke tests, update docstring to note scout no longer uses them. Pick **(b)** to preserve CAP-T036.
- `docs/CAPABILITIES.md` — CAP-B017, CAP-T043, CAP-T044, CAP-T045 registered. CAP-T036 description updated: SentinelWriter is now a public option, scout migrated to HTTP in BC-013.
- `memory/health-checks.md` — update § Scout analogue to note HTTP is now the primary path; file-sentinels remain available for HTTP-less services

---

## Non-goals

- Target-container health waterfall (BC-011)
- Prometheus / observability stack — IDEAS entry
- Multi-host scout coordination — IDEAS entry
- Removing SentinelWriter from `beaconry_health` — keep it for future HTTP-less services
- Making `make findings` also use the new table format — different story if operator wants uniformity

---

## Design decisions

**FastAPI + uvicorn in scout (not stdlib http.server).** Rejected: stdlib `http.server.ThreadingHTTPServer`. Reason: `build_router` returns a FastAPI `APIRouter` — using stdlib would mean reimplementing the four-endpoint convention in scout. FastAPI dependency weight (~15MB installed) is acceptable; scout already imports `requests` and `docker` SDK, so Python ecosystem overhead is already present.

**`asyncio.to_thread(collect_and_post)` for the scan loop.** Rejected: rewrite `syft_scan` and `requests.post` as async. Reason: syft is subprocess (blocking), requests is sync. Rewriting to aiohttp + asyncio.create_subprocess_exec is a bigger refactor than BC-013 should pursue. `asyncio.to_thread` is a one-liner that keeps the HTTP event loop responsive during long scans.

**Delete sentinel-file compose healthcheck.** Rejected: keep both for safety. Reason: convention drift. One source of truth per service.

**Keep SentinelWriter class in `beaconry_health`.** Rejected: delete it. Reason: future HTTP-less loop services (e.g. BC-011's target-probe worker, if that ever runs as a lightweight tick without HTTP) may want it. Mark as a legitimate option in the standard; scout no longer uses it.

**Table columns.** NAME / STATE / HEALTH / LAST SCAN / DETAILS. Rejected: NAME / UPTIME / HEALTH / NOTES — too close to `docker compose ps` but confusingly different. Rejected: NAME / STATUS (combined) — can't distinguish "container stopped" from "container running but degraded".

**Exit codes 0 / 1 / 2.** Rejected: just 0 and non-zero. Reason: operators scripting around `make status` want to distinguish "degraded, investigate" from "down, stack not up" — different incident classes.

**Human-readable error phrasing (no exception class names).** Rejected: show raw exceptions for debuggability. Reason: operators who need the raw traceback can run `python3 -m poc.scripts.status --verbose` later (follow-up IDEAS). Default output stays clean.

---

## Breaking changes

- Scout's compose healthcheck changes from sentinel-file test to HTTP probe → compose config diff; operators who override the healthcheck externally need to re-check
- Scout exposes `127.0.0.1:8001:8001` → new local port bound; operators with something else on 8001 will conflict (unlikely but documented in README)
- `make status` env knobs removed: `BEACONRY_SCOUT_DATA_DIR`, `BEACONRY_SCOUT_LIVE_MAX_AGE_S`, `BEACONRY_SCOUT_CONTAINER` → anyone exporting these will see them silently ignored; README to document
- `make status` output format changes from one-line summary to multi-row table → anyone grep'ing specific strings needs to update

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scout serves four HTTP endpoints via build_router | unit test | `test_scout_http_health.py::test_scout_router_exposes_four_endpoints` | ✓ |
| Scout /livez survives Docker socket failure | unit test | `test_scout_http_health.py::test_livez_survives_docker_socket_failure` | ✓ |
| Scout /readyz 503 when docker socket probe fails | unit test | `test_scout_http_health.py::test_readyz_reflects_docker_socket` | ✓ |
| Scout /healthz surfaces last_scan_ts + images_seen | unit test | `test_scout_http_health.py::test_healthz_includes_scan_metrics` | ✓ |
| Compose scout healthcheck targets /readyz | compose test | `test_BC_010_compose_health_chain.py::test_scout_healthcheck_targets_readyz` (new) | ✓ |
| Compose scout exposes 127.0.0.1:8001:8001 | integration test | `test_BC_010_compose_health_chain.py::test_scout_exposes_http_port` | ✓ |
| make status queries both services via HTTP (no docker exec) | unit test | `test_status_cli.py::test_no_docker_exec_called` | ✓ |
| make status table shape (NAME/STATE/HEALTH/LAST SCAN/DETAILS) | unit test | `test_status_cli.py::test_table_header_shape` | ✓ |
| make status all-healthy exit 0 | unit test | `test_status_cli.py::test_exit_0_all_healthy` | ✓ |
| make status any-degraded exit 1 | unit test | `test_status_cli.py::test_exit_1_hub_degraded` | ✓ |
| make status any-down exit 2 | unit test | `test_status_cli.py::test_exit_2_hub_down`, `test_status_cli.py::test_exit_2_both_down` | ✓ |
| make status output contains no raw exception class names | unit test | `test_status_cli.py::test_output_has_no_raw_exception_names` | ✓ |
| make status partial-up state (hub up, scout down) | unit test | `test_status_cli.py::test_partial_up_state_hub_up_scout_down` | ✓ |
| CAP-B017 + CAP-T043/044/045 registered | verify-traceability | `make check` traceability green | ✓ |
| Live: cold Compose up → make status prints healthy table | live manual | 2026-10-30 — `docker compose up -d` fired `Container beaconry-hub Healthy → beaconry-scout Started` chain; `make status` printed the 5-column table with both services STATE=running HEALTH=healthy; exit 0 | ✓ |
| Live: scout killed → table shows STATE=stopped within ~2s (not 5min) | live manual | 2026-10-30 — `docker kill beaconry-scout` → next `make status` within 2s showed scout STATE=down DETAILS="container not up"; hub row remained running+healthy independently. BC-010's 5-minute staleness window closed to ~1 second detection latency | ✓ |
| Live: grype DB removed → hub row HEALTH=degraded, exit 1 | live manual | 2026-10-30 — `docker exec beaconry-hub rm -rf /data/grype-db` → `python3 -m poc.scripts.status` returned exit code 1 (make converts to 2 by GNU convention — documented in README); table showed hub HEALTH=degraded DETAILS="degraded: grype_db missing", scout HEALTH=healthy; hub RestartCount: 0 → 0 (restart-loop trap still prevented) | ✓ |

Legend: ✓ · ⚠️ · ❌.

---

## Testing strategy

**Unit** (fast tier):
- `test_scout_http_health.py` — FastAPI TestClient against scout's app factory; mock Docker socket probe
- `test_status_cli.py` rewritten — mock urllib + `docker inspect` subprocess; assert table shape, exit codes, no exception class names in output

**Compose** (full tier):
- Existing BC-010 chain test updated for new scout healthcheck
- New: scout port 8001 is published to localhost

**Live** (Phase 5):
- Cold compose up; expect table with both rows healthy
- `docker kill beaconry-scout`; `make status` within 2s should report scout STATE=stopped
- `docker exec beaconry-hub rm -rf /data/grype-db`; expect hub row HEALTH=degraded with DETAILS naming `grype_db`
- Full down: `docker compose down`; expect two rows STATE=down, DETAILS hints at `docker compose up -d`

---

## Testing commands

```bash
source .venv/bin/activate
make check
make check-full
pytest poc/tests/unit/test_scout_http_health.py poc/tests/unit/test_status_cli.py -v --no-cov
cd poc && docker compose up --build
cd .. && make status                      # expect: table, exit 0
docker kill beaconry-scout && make status # expect: scout STATE=stopped, exit 2
docker compose down && make status        # expect: both STATE=down, exit 2
```
