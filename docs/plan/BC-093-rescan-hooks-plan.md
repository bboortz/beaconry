# BC-093 — Rescan triggers beyond SCAN_INTERVAL

*Branch: `feat/BC-093-rescan-hooks`*
*Start: 2026-11-05*
*Story: `docs/stories/BC-093-rescan-hooks.md`*

---

## Situation

Scout today scans on a single cadence (`SCAN_INTERVAL`, default 300s)
plus the BC-037 on-demand `/scan/trigger` endpoint. Three gaps per
design-decisions.md:
1. No wall-clock daily rescan — long-running hosts may miss slow
   drift between ticks if an operator sets `SCAN_INTERVAL` high.
2. No docker events listener — `docker pull nginx:1.27` sits
   invisible until the next SCAN_INTERVAL tick.
3. No pacman hook — `pacman -S openssl` delays detection by up
   to `SCAN_INTERVAL`.

## Problems to solve

- Slow drift between regular scans (daily rescan).
- Docker image pulls go unreported for up to SCAN_INTERVAL seconds.
- Package transactions same.

## Goals

1. Scout runs a configurable daily rescan.
2. Scout subscribes to docker events and triggers scans on relevant
   events, debounced.
3. Ship a pacman hook template operators can install.

## Scope

- `poc/scout/scout.py`:
  - New env `BEACONRY_DAILY_RESCAN_HOUR` (int 0-23, default `2`;
    -1 disables).
  - New env `BEACONRY_DOCKER_EVENTS_ENABLED` (default `1`).
  - New env `BEACONRY_DOCKER_EVENTS_DEBOUNCE_S` (default `10`).
  - New async task `_daily_rescan_loop_forever` — computes the next
    target wall-clock, sleeps until then, calls `_run_scan_once`
    with `reason="scheduled_daily"`.
  - New async task `_docker_events_loop_forever` — `docker.from_env
    ().events(filters={"type": "image", "event": ["pull"]})` +
    container `start`. Debounces via a monotonic timestamp; schedules
    at most one scan per `DEBOUNCE_S` window.
  - Both tasks started in lifespan; cancelled on shutdown.

- `poc/hooks/beaconry-pacman.hook` (new file) — alpm hook template
  for operators to install manually. Shipped under `poc/hooks/`
  not auto-installed.
- `poc/hooks/README.md` (new file) — installation + security notes
  (hook runs as root; scout endpoint must be local-only; no auth
  because /scan/trigger is unauthenticated in the current scout).

- Env docs (3 new), compose updates (3 new), VARIABLES.md (3 new).
- CAPs T191, T192, T193.
- INDEX.md BC-093 row.
- CHANGELOG v0.16.0.
- Tests: `poc/tests/integration/test_BC_093_rescan_hooks.py`.
- Version 0.15.0 → **0.16.0** (minor — three new features).

## Non-goals

- Apt/dnf/apk hooks.
- Delta-based "which image changed" tracking — full rescan per trigger.
- Hub-side orchestration — these triggers all live on the scout.
- Auth on `/scan/trigger` — out of scope (future BC).

## Design decisions

- **Wall-clock daily tick vs cron-style**: wall-clock. Scout
  computes next target and sleeps. Simpler than a cron parser;
  one knob (`HOUR`) covers 99% of operators.
- **Docker events `start` vs `create`**: subscribe to `pull` +
  `start` (actual runtime change) + `create` (new container).
  Skip `die`/`destroy` — removing a container doesn't change the
  CVE surface meaningfully; reconcile picks it up.
- **Debounce window**: single monotonic timestamp of last
  "scan-pending-for" plus a trailing asyncio task. A compose up
  that pulls 5 images → one scan 10 s later, not five back-to-back.
- **Pacman hook as template, not auto-install**: operator installs
  on their Arch host manually (needs root, needs to know their
  system). We ship the hook body + docs; no attempt to auto-detect
  Arch and silently install (that would surprise operators).
- **Both loop tasks are stand-alone background tasks**: separate
  from `_scan_loop_forever` so a crash in one doesn't take down the
  others. Each catches its own exceptions + logs.

## Breaking changes

### API / return shape

**None.** `/scan/trigger` unchanged (pacman hook just calls it).
No new HTTP endpoints.

### Data model

**None.**

### Behavioral semantics

- **Scouts fire more scans by default.** Docker events listener
  triggers on every image pull + container start. For operators
  with heavy container churn (frequent compose up/down), this is
  more hub traffic. Opt out via
  `BEACONRY_DOCKER_EVENTS_ENABLED=0`. Debounce keeps bursts sane.
- **Daily rescan at 02:00 local by default.** If scout's SCAN_INTERVAL
  is already 60s or less, this is largely redundant; operators who
  want strict cadence-only behaviour set `BEACONRY_DAILY_RESCAN_
  HOUR=-1`.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Scenario 1 — next-tick today | integration | `test_BC_093_rescan_hooks.py::test_daily_rescan_computes_next_tick_today` | ✓ |
| Scenario 1 — next-tick tomorrow | integration | `test_BC_093_rescan_hooks.py::test_daily_rescan_computes_next_tick_tomorrow` | ✓ |
| Scenario 1 — exactly at target hour | integration | `test_BC_093_rescan_hooks.py::test_daily_rescan_at_exact_hour` | ✓ |
| Daily env parse variants | integration | `test_BC_093_rescan_hooks.py::test_daily_rescan_env_parse[2-2]` | ✓ |
| Scenario 2 — daily disabled with -1 | integration | `test_BC_093_rescan_hooks.py::test_daily_rescan_disabled` | ✓ |
| Scenario 3 — docker events trigger | integration | `test_BC_093_rescan_hooks.py::test_docker_event_triggers_scan` | ✓ |
| Scenario 4 — events debounce | integration | `test_BC_093_rescan_hooks.py::test_docker_events_debounce` | ✓ |
| Scenario 5 — events disabled | integration | `test_BC_093_rescan_hooks.py::test_docker_events_disabled` | ✓ |
| Docker events default enabled | integration | `test_BC_093_rescan_hooks.py::test_docker_events_default_enabled` | ✓ |
| Debounce default = 10s | integration | `test_BC_093_rescan_hooks.py::test_docker_events_debounce_default` | ✓ |
| Debounce clamps to ≥1 | integration | `test_BC_093_rescan_hooks.py::test_docker_events_debounce_clamp` | ✓ |
| Scenario 6 — pacman hook template | integration | `test_BC_093_rescan_hooks.py::test_pacman_hook_file_present_and_well_formed` | ✓ |
| Hooks README shipped | integration | `test_BC_093_rescan_hooks.py::test_hooks_readme_present` | ✓ |
| VARIABLES + env + compose sync | lint | `make verify-variables` + `verify-compose-env-propagation` | ✓ |
| CAP-T191/T192/T193 registered | lint | `make verify-traceability` | ✓ |
| Fast tier green | gate log | `make check` | ✓ |

## Rollout

- Hub boots unchanged.
- Scout boots with daily + docker-events loops active by default.
- Operator with Arch host: copies `poc/hooks/beaconry-pacman.hook`
  to `/etc/pacman.d/hooks/`.
- Operators who don't want the new triggers set the env to 0 / -1.
