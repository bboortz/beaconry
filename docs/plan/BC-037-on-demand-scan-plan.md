# BC-037 — On-demand scout scan trigger (`make scan`)

*Branch: `feat/BC-037-on-demand-scan`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-037-on-demand-scan.md`*

---

## Situation

Scout runs on a fixed `SCAN_INTERVAL` cadence (default 300s). After an operator applies an out-of-band fix (`docker compose pull`, package upgrade), there is no way to re-verify beaconry's view of the inventory without waiting up to the full interval. For single-finding "Fix now" taps BC-033 already covers it (`verify_fix` → single-image grype re-scan). For multi-service changes outside the Telegram flow — the typical homelab operator workflow — there's a 0–300s silence after a change.

---

## Problems to solve

- No manual trigger for a full scout cycle.
- Operators who batch-upgrade multiple services (compose stack rebuild) have no way to force a re-verify.
- The `--once` scout CLI flag exists but spawns a new process; it bypasses the running loop's state tracking and would require shell access to the scout container.

---

## Goals

1. Scout exposes `POST /scan/trigger` returning `202 Accepted` with a background task that runs `collect_and_post()` async.
2. Concurrent-run guard: a second trigger while one is in-flight returns `409 Conflict` without starting a second scan.
3. Trigger does NOT reset or shift the SCAN_INTERVAL loop timer — the two timelines are independent.
4. New `make scan` target curls the endpoint through the host-reachable port (`127.0.0.1:8001`), surfaces the response, and exits non-zero on error.

---

## Scope

- `poc/scout/scout.py`:
  - New module-level `_scan_in_progress: bool` flag.
  - Wrap `collect_and_post()` in a thin `_run_scan_once()` async that acquires + releases the flag.
  - Shared helper so both the loop and the trigger use the same guarded path.
  - New `POST /scan/trigger` route on `app` (added after `app.include_router(build_router(...))`).
  - Trigger launches `asyncio.create_task(_run_scan_once(reason="manual"))` so the handler returns immediately.
- `Makefile`:
  - New `scan` target that curls `http://127.0.0.1:8001/scan/trigger` and pretty-prints the response.
  - Includes a pre-check: scout container running, port reachable; actionable error otherwise.
- `poc/tests/unit/test_scan_trigger.py`:
  - `_run_scan_once` holds the lock during execution.
  - Concurrent `_run_scan_once` returns the "already running" sentinel.
  - Loop + trigger share the same guard (both go through the same helper).
- `poc/tests/integration/test_BC_037_on_demand_scan.py`:
  - FastAPI TestClient covering: 202 happy path, 409 concurrency path, response shape.
- `docs/CAPABILITIES.md` — register CAP-T081, CAP-B021.
- `docs/stories/BC-037-on-demand-scan.md` — status flip.
- `docs/stories/INDEX.md` — BC-037 row.

---

## Non-goals

- Per-image targeted scan. V1 is the full inventory cycle.
- Authentication / rate limiting on the trigger. Scout's HTTP binds `127.0.0.1` only; same trust envelope as the rest of the local-only compose stack.
- Scheduling future scans. SCAN_INTERVAL remains the only scheduler.
- Replaying a historical scan. Each trigger is a fresh real-time cycle.

---

## Design decisions

- **Mutex is a bool, not a lock**: scout's event loop is single-threaded; a bool guarded by async semantics is enough. Avoids importing `threading.Lock`.
- **Trigger returns 202 immediately** (not 200 blocking until scan completes): grype matching can take 60–90s. Blocking the HTTP call that long is unfriendly for a Make target. Response includes `{"status": "scheduled", "started_at": ts}` so the operator can tail scout logs if they want detail.
- **No loop timer reset**: a manual scan should not postpone the next scheduled one. The two counters are independent. Prevents an operator who runs `make scan` repeatedly from unintentionally halving their scan cadence.
- **Rejected: expose the trigger as a GET** — "trigger" is a mutation. POST is the honest verb even though no body is required.
- **Rejected: new CLI subcommand** — Makefile target is the operator-facing shape in this project; CLI would duplicate.

---

## Breaking changes

### 1. API / return shape

- **Scout gains `POST /scan/trigger`**. Older operators of this scout have no code path that calls it — pure additive. Older hubs are unaffected (not a hub endpoint).
- `/scan/trigger` returns:
  - `202 Accepted` with `{"status": "scheduled", "started_at": <unix_ts>}` on happy path.
  - `409 Conflict` with `{"status": "already_running", "started_at": <unix_ts>}` on concurrent request.
  - `503 Service Unavailable` if the Docker socket is unavailable (prerequisite for `collect_and_post`).

### 2. Data model (schema + legacy rows)

**None.** No DB column or table added. The in-progress flag is in-memory per scout instance — survives scout restart as False (no half-done scan to preserve).

### 3. Behavioral semantics

- Operators who had a hands-off scout gain a new control surface. Default behavior unchanged: `make scan` is opt-in, scout never calls `/scan/trigger` itself.
- The scheduled loop is **not** shifted by a trigger — intentional per Design Decisions, documented in the Gherkin.
- A manual scan during a scheduled scan hits the 409 branch; neither scan is lost.

**No implicit opt-out needed**: this is pure new capability.

---

## Merge plan

1. All work on `feat/BC-037-on-demand-scan`.
2. `make check` + `make check-full` must pass.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward.
5. Post-merge: story → `shipped`, INDEX update, `make live-rebuild`, verify by running the new `make scan` against the live stack.

---

## Rollout plan

Additive. On upgrade, operators who do nothing see no change — background loop runs at SCAN_INTERVAL as before. `make scan` is a new tool they can discover via the Makefile help, `.env.example`, or docs.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| `make scan` happy path returns 202 | integration | `test_BC_037_on_demand_scan.py::test_trigger_returns_202` | ✓ |
| Concurrent trigger returns 409 | integration | `test_BC_037_on_demand_scan.py::test_concurrent_trigger_returns_409` | ✓ |
| Response shape carries status + started_at | integration | `test_BC_037_on_demand_scan.py::test_trigger_response_shape` | ✓ |
| `_run_scan_once` holds the in-progress flag during execution | unit | `test_scan_trigger.py::test_run_scan_once_holds_flag` | ✓ |
| Concurrent `_run_scan_once` short-circuits | unit | `test_scan_trigger.py::test_concurrent_scan_short_circuits` | ✓ |
| Loop + manual trigger share the same guard | unit | `test_scan_trigger.py::test_loop_and_manual_share_guard` | ✓ |
| Manual trigger does NOT shift the scheduled loop timer | unit | `test_scan_trigger.py::test_manual_trigger_does_not_shift_loop_timer` | ✓ |
| Docker socket missing → 503 | integration | `test_BC_037_on_demand_scan.py::test_trigger_503_when_docker_unavailable` | ✓ |
| `make scan` surfaces errors when scout container down | manual | verified 2026-10-31: `docker stop beaconry-scout && make scan` → exit 1 + "✗ scout container not running" | ✓ |
| CAP-T081 + CAP-B021 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| Makefile help lists `scan` | lint | `make help` output; `verify-docs-drift` | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

- Unit tests pin the mutex semantics (flag held during execution, concurrent calls short-circuit, loop + manual share the same guard, loop timer unaffected).
- Integration tests exercise the HTTP route via FastAPI TestClient — same shape as `test_scout_http_health.py` already uses.
- Live verification post-merge: `make scan` on the running stack, observe scout log, run again immediately → observe 409.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_scan_trigger.py poc/tests/integration/test_BC_037_on_demand_scan.py -v
# Post-merge:
make scan              # happy path
make scan ; make scan  # second one hits 409
```
