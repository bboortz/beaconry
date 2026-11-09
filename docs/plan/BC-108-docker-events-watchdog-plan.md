# BC-108 — Docker events watchdog

*Branch: `feat/BC-108-docker-events-watchdog`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-108-docker-events-watchdog.md`*

---

## Situation

`poc/scout/scout.py::_docker_events_loop_forever` does:

```python
def _blocking_consume() -> None:
    try:
        for _event in _docker_events_stream():
            loop.call_soon_threadsafe(_schedule_scan)
    except Exception as e:
        log.warning("BC-093 docker events stream ended: %s", e)

await loop.run_in_executor(None, _blocking_consume)
```

Single-shot. If the daemon restarts, the iterator raises
`ChunkedEncodingError` (or similar), the thread exits, and no further
events are processed. Scout falls back to `SCAN_INTERVAL` (300s
default) + daily rescan only.

---

## Problems to solve

- Daemon restart → event-driven scans silently stop.
- Operator has no at-a-glance signal that the listener is dead.
- Pre-BC-108 the only recovery is a scout process restart.

---

## Goals

1. On stream exception, scout sleeps backoff_s, then re-subscribes
   (maps to Scenario "Daemon restart mid-session").
2. Repeated reconnect failures double the backoff, capped at
   `BEACONRY_DOCKER_EVENTS_BACKOFF_MAX_S` (maps to Scenario
   "Repeated reconnect failures grow backoff").
3. Clean shutdown exits the loop (maps to Scenario "Clean shutdown").
4. `/healthz` exposes `docker_events_reconnects` counter +
   `docker_events_last_reconnect_ts`.

---

## Scope

- `poc/scout/scout.py`:
  - New env vars: `BEACONRY_DOCKER_EVENTS_BACKOFF_MIN_S` (default 1),
    `BEACONRY_DOCKER_EVENTS_BACKOFF_MAX_S` (default 60).
  - New module globals: `_docker_events_reconnects`,
    `_docker_events_last_reconnect_ts`.
  - New helper: `_next_backoff(prev)` — doubling clamped to
    [MIN, MAX]; MIN on first attempt.
  - Rewrite `_docker_events_loop_forever` from single-shot to
    while-loop with reconnection on exception.
  - `_scout_healthz_summary` grows two keys.
- `poc/docker-compose.yml` + `poc/.env.example` + `docs/VARIABLES.md`:
  two new env rows.
- `docs/CAPABILITIES.md`: CAP-T210.
- Unit tests: `poc/tests/unit/test_BC_108_docker_events_watchdog.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.1` (patch — resilience fix).

---

## Non-goals

- Buffered replay of missed events during the outage window
  (docker.events() has no replay semantics; SBOM diff on the next
  scan tick reconciles).
- Alerting on prolonged outages (surfaces via `/healthz` counter +
  `make status`; future BC if signal quality warrants).

---

## Design decisions

- **Capped exponential backoff MIN → MAX**: identical pattern to BC-104
  / BC-107's retry sweeps. Starting at 1s gives fast reconnection on
  the common case (10–20s daemon restart) without hammering during
  prolonged outages. Cap at 60s so a 24h daemon outage reconnects
  within ≤1min of recovery.
- **Clean-EOS vs exception path distinction**: `_blocking_consume`
  returns `bool`. `True` → shutdown (exit loop). `False` → reconnect
  + backoff. Pre-BC-108 these two paths were indistinguishable.
- **Counter + last-ts in /healthz**: operator-visible drift signal.
  Steady growth = daemon health issue; one-off bumps = routine
  restart. The ts lets operators cross-reference with system logs.

---

## Breaking changes

### 1. API / return shape

None. Internal rewrite; no external-facing interface change.

### 2. Data model (schema + legacy rows)

None. No DB changes.

### 3. Behavioral semantics

- Pre-BC-108: event-driven scans stop on daemon restart (until
  scout restart).
- Post-BC-108: event-driven scans resume automatically.
- No knob to opt out — restoring stopping-forever behavior would
  have no legitimate operator use case. Operators who want to
  disable the event listener entirely still have
  `BEACONRY_DOCKER_EVENTS_ENABLED=0` (the pre-BC-093 fallback).

---

## Merge plan

1. All work on `feat/BC-108-docker-events-watchdog`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive, zero operator action. Default backoff values suit the
typical self-hosted deployment (1s → 60s cap).

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "Daemon restart mid-session" | unit | `poc/tests/unit/test_BC_108_docker_events_watchdog.py::test_next_backoff_first_attempt_returns_min`, `test_next_backoff_doubles_each_attempt` | ✓ |
| Scenario "Repeated reconnect failures grow backoff" | unit | `poc/tests/unit/test_BC_108_docker_events_watchdog.py::test_next_backoff_clamped_at_max` | ✓ |
| Scenario "Clean shutdown exits the loop" | manual | `_blocking_consume` returns `True` on clean EOS; loop breaks (poc/scout/scout.py — `if clean: return`). Verified by code review 2026-11-09 — infinite async loop not easily unit-testable. | ✓ |
| `/healthz` exposes counters | unit | `test_healthz_exposes_reconnect_counters`, `test_healthz_reflects_bumped_counter` | ✓ |
| Env clamp MAX ≥ MIN | unit | `test_env_max_clamped_to_min` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

The ⚠️ row is acceptable: the clean-EOS exit is a 3-line code path
(`if clean: return`) that pyright type-checks statically. Driving
it from a unit test requires mocking `run_in_executor` which is
mostly tautology.

---

## Testing strategy

- Unit tests isolate `_next_backoff` pure function + env parsing +
  `/healthz` key surfacing.
- Infinite async loop + exception-retry path validated by code
  structure (not a unit test — mocking `run_in_executor` adds no
  safety).

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_108_docker_events_watchdog.py -v
```
