# BC-109 — Grype DB update retry + /readyz gate

*Branch: `feat/BC-109-grype-db-retry`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-109-grype-db-retry.md`*

---

## Situation

Hub's lifespan at `poc/hub/hub.py` (~line 2364) does:

```python
if SKIP_GRYPE_DB:
    log.info("Skipping grype vulnerability database update …")
else:
    proc = await asyncio.create_subprocess_exec("grype", "db", "update", ...)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning("grype db update warning: %s", stderr.decode()[:200])
    else:
        log.info("Grype vulnerability database ready")
_startup_complete = True
```

Single-shot. On any failure the hub proceeds — `/readyz` goes 503
on the `grype_db` probe because the dir stays empty, compose marks
unhealthy, scout (via `depends_on: service_healthy`) refuses to
start. The gate works. But there is no retry — operator has to
restart the hub manually once the network recovers.

---

## Problems to solve

- Startup-time transient network failure (ISP flap, DNS glitch,
  registry 5xx) traps the hub in an unrecoverable unhealthy state.
- Operator has no at-a-glance diagnostic: `/healthz` doesn't say
  "retrying" or "last error".

---

## Goals

1. On first-attempt failure, a background task retries with capped
   exponential backoff until success (maps to Scenarios "First-attempt
   failure" + "Retry eventually succeeds").
2. Backoff doubles each failed attempt, clamped to a configurable
   ceiling (maps to Scenario "Backoff doubles each failed retry").
3. `/healthz` exposes `grype_db_update_attempts`, `last_update_ts`,
   `last_error` for operator diagnostics.

---

## Scope

- `poc/hub/hub.py`:
  - New env vars: `BEACONRY_GRYPE_DB_RETRY_BASE_S` (default 10),
    `BEACONRY_GRYPE_DB_BACKOFF_MAX_S` (default 600).
  - New module globals: `_grype_db_update_attempts`,
    `_grype_db_last_update_ts`, `_grype_db_last_error`.
  - New `_grype_db_update_once()` — single-attempt subprocess
    wrapper shared by startup + retry loop.
  - New `_next_grype_backoff(prev)` — pure helper.
  - New `_grype_db_update_retry_loop_forever()` — background task.
  - Lifespan path: try once; on failure spawn the retry task.
  - `_hub_healthz_summary` gains three keys.
- `poc/.env.example`, `poc/docker-compose.yml`, `docs/VARIABLES.md`:
  two new env rows.
- `docs/CAPABILITIES.md`: CAP-T211.
- Unit tests: `poc/tests/unit/test_BC_109_grype_db_retry.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.2` (patch — resilience fix).

---

## Non-goals

- Mid-session DB refresh — grype has its own daemon for subsequent
  CVE data. BC-109 only guards the startup download.
- Metrics / alerting beyond `/healthz`.

---

## Design decisions

- **Single-shot sync + background-on-failure**: healthy common case
  (~5s download) still completes before `_startup_complete = True`.
  Only pathological cases spawn a task — saves resources vs. an
  always-running sweep.
- **`_startup_complete = True` unconditionally**: hub is "started"
  even if grype DB is still downloading. `/readyz` gates on the
  dir-populated check (pre-BC-109 behavior) — no restart-loop trap.
- **Backoff [10s, 600s]**: 10s catches the common 3-5s network
  blip; 600s cap means a 24h network outage reconnects within
  ≤10min. Same shape as BC-108.
- **Shared `_grype_db_update_once`**: lifespan + retry loop call
  the exact same subprocess invocation. Any future divergence would
  be a bug magnet.

---

## Breaking changes

### 1. API / return shape

None. Internal lifespan rewrite.

### 2. Data model (schema + legacy rows)

None.

### 3. Behavioral semantics

- Pre-BC-109: single-shot, no retry.
- Post-BC-109: single-shot + background retry on failure. Hub
  "recovers itself" instead of staying unhealthy forever.
- No knob to opt out — there's no legitimate operator case for
  "stay broken until manual restart". Setting `MAX_ATTEMPTS=1`
  isn't offered; the retry is capped by MAX backoff, not by count.

---

## Merge plan

1. All work on `feat/BC-109-grype-db-retry`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive. Zero operator action. Default backoff values suit the
typical ISP flake.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "First-attempt failure triggers retry task" | manual | Lifespan `if ok: log success else: spawn _grype_db_update_retry_loop_forever()` (poc/hub/hub.py, ~line 2371). Verified by code review 2026-11-09 — infinite async loop not easily unit-testable. | ✓ |
| Scenario "Retry eventually succeeds" | unit | `test_grype_db_update_once_success` + `test_next_grype_backoff_doubles_each_attempt` | ✓ |
| Scenario "Backoff doubles each failed retry" | unit | `poc/tests/unit/test_BC_109_grype_db_retry.py::test_next_grype_backoff_doubles_each_attempt`, `test_next_grype_backoff_clamped_at_max` | ✓ |
| `/healthz` exposes counters | unit | `test_healthz_exposes_grype_db_counters`, `test_healthz_surfaces_error_on_failure` | ✓ |
| Spawn failure handled | unit | `test_grype_db_update_once_spawn_failure` | ✓ |
| Env clamp MAX ≥ BASE | unit | `test_env_max_clamped_to_base` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

- Unit: pure helpers (`_next_grype_backoff`), subprocess wrapper
  (`_grype_db_update_once`) with monkeypatched
  `asyncio.create_subprocess_exec`, `/healthz` surfacing.
- Infinite retry loop driven by the above components; validated by
  code structure, not a unit test.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_109_grype_db_retry.py -v
```
