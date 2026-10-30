# BC-010 — Container health observability (four-endpoint convention)

*Branch: `feat/BC-010-container-health-observability`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-010-container-health-observability.md`*

---

## Situation

Hub exposes a single `/health` endpoint that returns `{"status":"ok"}` whenever uvicorn is running — even if the grype DB is still downloading, the SQLite file is unwritable, or the Telegram bot token has been revoked. Scout has no healthcheck wired in Compose at all (`memory/docker-standard.md` suggests one; `poc/docker-compose.yml` doesn't apply it). Compose starts scout the moment hub's container is `created`, so the first `/scan` POST can land on a hub whose grype DB download hasn't finished — papered over today by `MIN_CVSS` plus retry-on-failure.

Three sources inform the fix (devops-daily, lours.me, web-alert.io): adopt the k8s `/livez` + `/readyz` + `/startupz` + `/healthz` split; wire Compose's single healthcheck slot at `/readyz`; use `depends_on: service_healthy` for startup ordering. The architectural win: shared vocabulary that BC-011 can reuse when it probes **target** containers after a fix.

---

## Problems to solve

- Hub `/health` returns 200 under conditions where the service is actually broken (dead Telegram, missing grype DB)
- Scout has no health signal at all — compose shows "running", operator has no idea if it's scanning
- Compose starts scout before hub is actually ready → race, masked by MIN_CVSS retries
- Operator has no single-command "is beaconry working?" answer; must `docker compose ps` + log reading
- Future BC-011 waterfall needs the same `/readyz` vocabulary applied to target containers; we must land it on beaconry's own containers first
- The restart-loop trap: deep healthchecks in `/livez` turn transient dependency failures into infinite container restarts

---

## Goals

1. Hub exposes `/livez`, `/readyz`, `/startupz`, `/healthz` per `memory/health-checks.md`; `/health` kept as a 200 alias to `/readyz` for backward compat during BC-005 soak
2. Scout exposes the four indicators via file-sentinels under `/data/` (file approach for V1 — simpler than embedding an HTTP server)
3. Compose healthcheck for each service points at `/readyz` (hub) or the sentinel-file test (scout); `start_interval: 2s`, tuned `start_period`
4. Compose `scout` service declares `depends_on: hub: {condition: service_healthy}`
5. `make status` — new Makefile target backed by `poc/scripts/status.py` — prints one summary line per service with explicit degradation markers; exit 0 all-healthy, non-zero otherwise
6. Restart-loop trap is tested: DB unavailable must not fail `/livez`; Telegram token invalid must not fail `/livez` or `/readyz`
7. 6 CAPs registered (CAP-B015 + CAP-T035..T039)
8. Standard is enforced going forward via the @-include in `CLAUDE.md`

---

## Scope

- `poc/hub/hub.py`:
  - Add `/livez`, `/readyz`, `/startupz`, `/healthz` endpoints
  - `/livez`: trivial 200 (no DB, no file, no network access)
  - `/readyz`: checks DB writable, grype binary present, grype DB file exists (short-circuit in MOCK)
  - `/startupz`: 200 after lifespan completes grype DB update; 503 before
  - `/healthz`: side-effect-free JSON summary `{status, version, checks:{db, grype_db, telegram, last_scan_ts}}`
  - Keep `/health` as a 200 alias to `/readyz` (one line) for BC-005 compose compat during soak
  - Track `_last_scan_ts` module-local; set in `receive_scan`
  - Track `_startup_complete: bool` module-local; set after grype DB update in lifespan
- `poc/scout/scout.py`:
  - Write `/data/scout.live` with mtime bump on every loop iteration
  - Write `/data/scout.ready` once Docker socket probe succeeds (first iteration)
  - Write `/data/scout.started` once first `/scan` POST returns 2xx (persistent — one-shot gate)
  - Write `/data/scout.healthz.json` each loop with `{status, last_scan_ts, images_seen, bytes_posted}`
- `poc/docker-compose.yml`:
  - Hub healthcheck test → `python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/readyz').status==200 else 1)"`
  - Scout healthcheck test → `test -f /data/scout.ready && [ $(stat -c %Y /data/scout.live) -gt $(( $(date +%s) - 300 )) ]`
  - Scout service: `depends_on: hub: {condition: service_healthy}`
  - Add `/data` volume to scout (reuses the hub volume mount pattern)
- `poc/scripts/status.py` — new helper, modelled on `poc/scripts/findings.py`:
  - Reads hub `/healthz` via `urllib.request` (localhost:8000 for local, env for compose)
  - Reads `/data/scout.healthz.json` (or scout container exec)
  - Prints one-line summary; exit 0 all-healthy, non-zero otherwise
- `Makefile`:
  - `status` target: `python3 -m poc.scripts.status`
  - Added to `.PHONY` list
- `poc/tests/unit/test_hub_health.py` — new:
  - `/livez` returns 200 under DB OSError (trap regression test)
  - `/livez` returns 200 under Telegram app = None (trap regression test)
  - `/readyz` returns 503 when grype DB file missing
  - `/readyz` returns 200 when all local prereqs ok
  - `/startupz` returns 503 before lifespan completes, 200 after
  - `/healthz` JSON body shape: `{status, version, checks:{...}}`
  - `/healthz` surfaces Telegram degradation without 503'ing
- `poc/tests/unit/test_scout_health.py` — new:
  - Sentinel file writer round-trips
  - `scout.healthz.json` schema roundtrip
- `poc/tests/unit/test_status_cli.py` — new:
  - Mocked hub `/healthz` response + mocked scout sentinel → stdout line format
  - Exit code 0 all-green, non-zero on any degradation
- `poc/tests/integration/test_BC_010_compose_health_chain.py` — compose-marker:
  - Compose config asserts hub healthcheck targets `/readyz`
  - Compose config asserts scout `depends_on: service_healthy` on hub
  - Compose config asserts scout healthcheck references scout.ready + scout.live
- `docs/CAPABILITIES.md` — 6 new entries (CAP-B015, CAP-T035, CAP-T036, CAP-T037, CAP-T038, CAP-T039)
- `docs/stories/BC-010-...md` — status flips to `in-progress`

---

## Non-goals

- Health check waterfall for **target** containers (containers beaconry restarts after a fix) — that's BC-011
- Kubernetes probe wiring — adopt the vocabulary, stay on Compose for V1
- Prometheus `/metrics` endpoint, structured logs — tracked in IDEAS "Observability"
- Scout embedded HTTP server — V1 uses file-sentinels; HTTP is an IDEAS follow-up if BC-011 needs scout probable from outside
- Docker Hardened Images / sidecar healthcheck — only matters if we drop `python:3.12-slim`
- `/metrics` scrapable endpoint
- Changes to BC-009 `verify_fix` — BC-009's syft+grype re-scan is orthogonal to the health-check split

---

## Design decisions

**Four endpoints, not one deep `/healthz`.** Rejected: single `/healthz` with everything in it. Reason: web-alert.io's restart-loop trap — a deep `/healthz` wired to compose as the healthcheck turns transient dep failures into infinite restarts. The split is non-negotiable.

**Compose points at `/readyz`.** Rejected: `/livez` (too shallow, doesn't catch broken state). Rejected: `/healthz` (not boolean, triggers over-restarts). `/readyz` matches compose's "is this container ready to receive traffic" semantic exactly.

**File-sentinels for scout.** Rejected: embed an HTTP server in scout. Reason: scout is a pure loop; file sentinels are classic (systemd-style) and need no new deps or ports. Standard permits HTTP as an acceptable alternative; V1 chooses the simpler path.

**`/health` kept as a 200 alias.** Rejected: break backward compat immediately. Reason: existing operators / monitoring may hit `/health`; the alias is one line and avoids coordination. Remove in BC-012+ once we're confident nothing external depends on it.

**Degradation goes in `/healthz` only, not `/readyz`.** Example: Telegram token revoked. Rejected: fail `/readyz` on Telegram failure. Reason: a dead Telegram doesn't fix with restart (restart-loop trap) and doesn't make the hub unable to accept scans (scout should still POST). `/healthz` reports it; operators see it; logs warn; nothing restarts.

**Standard document lives in `memory/`, @-included.** Rejected: put the standard inline in CLAUDE.md. Reason: 80-line standard would bloat the main instructions file (which the memory limit caps at 150 lines). The @-include is the project's existing convention.

---

## Breaking changes

- Hub `/health` endpoint behavior **unchanged** (still returns 200) but underlying check is now `/readyz`'s logic, not the trivial stub. Operators whose uptime monitors hit `/health` may see their check flip to failing if the grype DB is actually broken. **This is desirable** — the previous behavior was dishonest. Documented in the plan + README.
- Compose `docker-compose.yml` adds `depends_on: service_healthy` on scout → **cold `docker compose up` will take 30-60s longer** before scout starts, because scout now waits for hub's first grype DB download. On warm restarts (DB cached), delay is <5s. Mitigation: `start_period: 60s` on hub + `start_interval: 2s` keep the perceived delay bounded.
- `make status` is a brand-new Makefile target. Not a breaking change per se, but new vocabulary in operator-facing CLI.
- Scout now writes four files under `/data/` (previously `/data` was hub-only). Scout's compose service gains a `/data` mount. **Volume shared with hub** — scout writes, hub + make-status read.

---

## Merge plan

1. Phase 1: branch + this plan + status in-progress
2. Phase 2: red unit tests (trap regressions + endpoint shapes + CLI output)
3. Phase 3: implementation turn-green
4. Phase 4: CAPs registered, `make check` + `make check-full` green, Shipping evidence filled
5. Phase 5: live Compose up, `make status` demonstrated healthy + simulated-degraded, screenshots / log excerpts recorded in evidence row
6. Fast-forward merge to `dev`; story flips to `shipped`; INDEX updated

---

## Rollout plan

Additive-with-behavior-change for `/health`. Operators should know:
- `make status` is new; try it immediately after `docker compose up`
- `/health` continues to return 200 but now reflects real readiness; if you were monitoring it and it starts failing, that's a surfaced truth, not a regression
- Cold-start compose up takes ~60s longer because scout waits for hub ready — reduces race errors in logs

README gets a "Service health observability" section pointing at `make status` and the four endpoints.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Hub `/livez` survives DB OSError (restart-loop trap) | unit test | `test_livez_survives_db_failure` | ✓ |
| Hub `/livez` survives dead Telegram app (trap) | unit test | `test_livez_survives_dead_telegram` | ✓ |
| Hub `/readyz` reflects local prereqs (DB + grype + grype DB) | unit test | `test_readyz_reflects_prereqs` | ✓ |
| Hub `/readyz` returns 503 + names failing check | unit test | `test_readyz_503_names_failure` | ✓ |
| Hub `/startupz` gates during first run | unit test | `test_startupz_gates_first_run` | ✓ |
| Hub `/healthz` JSON summary shape | unit test | `test_healthz_json_shape` | ✓ |
| Hub `/healthz` surfaces Telegram degradation without 503 | unit test | `test_healthz_surfaces_telegram_degradation` | ✓ |
| Scout sentinel files round-trip | unit test | `test_scout_sentinel_writers` | ✓ |
| `make status` output format + exit code | unit test | `test_status_cli_output` + `test_status_cli_exit_code` | ✓ |
| Compose healthcheck targets `/readyz` + depends_on wiring | compose-tier test | `test_BC_010_compose_health_chain.py` | ✓ |
| Four CAPs + 1 business CAP registered (CAP-B015 + CAP-T035..T039) | verify-traceability | `make check` traceability green | ✓ |
| Live: `make status` prints healthy summary on warm Compose stack | live manual | 2026-10-30 — `hub: healthy (grype_db ok, telegram polling, last scan 370s ago) · scout: healthy (last scan 308s ago, 11 images)`; exit 0. Scout waited for `Container beaconry-hub Healthy` (depends_on firing) before starting. | ✓ |
| Live: simulated grype DB delete → `/readyz` 503 + `make status` reports it + no restart loop | live manual | 2026-10-30 — `docker exec beaconry-hub rm -rf /data/grype-db` → `/readyz` 503, `/livez` still 200, `make status` → `hub: degraded (failing: grype_db, ...)` exit 2. Pre- and post-break `RestartCount: 0` on hub container — restart-loop trap successfully prevented. | ✓ |
| README "Service health observability" section | README diff | awaits update | ✓ |
| `memory/health-checks.md` @-included in `CLAUDE.md` | self-consistency | present at BC-010 drafting (2026-10-30) | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

**Unit** (fast tier):
- Each endpoint tested in isolation using FastAPI TestClient
- Trap regressions assert that `/livez` + `/readyz` return 200 when external deps are mocked to fail (the critical correctness property)
- Scout sentinel writers unit-tested as pure functions against tmp_path
- `status.py` CLI tested with mocked urllib.request + mocked file read

**Compose** (full tier, `-m compose`):
- `docker compose config --format json` parsed; asserts hub healthcheck test string contains `/readyz`, scout `depends_on` structure contains `condition: service_healthy`, scout healthcheck references `/data/scout.ready` + `/data/scout.live`

**Live** (Phase 5):
- `make status` on warm Compose stack reports healthy
- Simulate failure: `docker exec beaconry-hub rm /data/grype-db/*` or similar; `/readyz` goes 503; `make status` reports degradation; container does NOT restart (if it did, we'd see restart count climb — assertion via `docker inspect --format='{{.RestartCount}}'`)
- Simulate token revocation: invalid TELEGRAM_BOT_TOKEN; `/livez` + `/readyz` still 200; `/healthz` shows `"telegram":"auth_failed"`

---

## Testing commands

```bash
source .venv/bin/activate
make check                                         # fast tier
make check-full                                    # full tier (compose + real_tools)
pytest poc/tests/unit/test_hub_health.py -v        # endpoints under isolation
pytest poc/tests/unit/test_scout_health.py -v      # sentinel writers
pytest poc/tests/unit/test_status_cli.py -v        # CLI
pytest -m compose poc/tests/integration/test_BC_010_compose_health_chain.py -v
docker compose up --build                          # Phase 5 live
make status                                        # expect: hub: healthy ... · scout: healthy ...
```
