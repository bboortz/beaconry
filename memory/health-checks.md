*Health-check convention. Adopted BC-010 (2026-10-30). Applies to **every** container beaconry ships now and in the future.*

## The four endpoints

Every beaconry service exposes four HTTP endpoints at module-root (or, for services without an HTTP surface, the file-sentinel equivalents in § Scout analogue below). Each answers a different question with a specific orchestrator reaction.

| Endpoint | Question it answers | What it checks | What it must NOT check | Fail reaction |
|---|---|---|---|---|
| `/livez` | process alive, not deadlocked? | the event loop responds — nothing more | **no DB, no external deps**: a dead DB must not fail liveness (restarting the pod won't fix the DB; infinite restart loop) | compose → restart container |
| `/readyz` | ready to accept work right now? | local prerequisites: DB file writable, grype binary present, grype DB file on disk, Docker socket openable (where used) | optional dependencies; anything > ~1s latency; Telegram reachability (degraded state, not unready) | compose → mark unhealthy, don't route traffic |
| `/startupz` | first-run boot completed? | one-shot gates: grype DB downloaded (hub), first inventory posted (scout) | anything checked by `/readyz` (`/startupz` gates `/readyz` and `/livez` during start_period) | compose → delay `/livez`/`/readyz` probes during `start_period` |
| `/healthz` | operator-facing summary | JSON: `{"status":"ok","version":"...","checks":{"db":"ok","grype_db":"ok","telegram":"polling","last_scan_s":42}}` | writes; must be side-effect free | human curl, `make status`, future observability stack |

Status codes: 200 if the question is answered "yes", 503 if "no" (never 500 for known degraded states — reserve 500 for actual bugs). `/healthz` always returns 200 with the body describing degradation.

## The restart-loop trap (do not step in it)

> *"If your database is down, your liveness check should still pass — restarting the pod won't fix the database."* — web-alert.io

Mapping that rule onto beaconry: Telegram token revoked → hub can no longer notify, but `/livez` and `/readyz` **must still return 200**. `/healthz` surfaces the degradation (`"telegram":"auth_failed"`), logs warn, operator sees it in `make status`. Nothing restarts. Nothing drops out of the LB.

Rule of thumb: before adding a check to `/livez` or `/readyz`, ask *"would restarting the container fix this failure?"* If no, the check belongs in `/healthz` only.

## Compose consumes only one healthcheck — point it at `/readyz`

Compose has no per-probe type knob. Policy: healthcheck → `/readyz`. The other endpoints exist for `make status`, future k8s migration, and operator curl.

```yaml
services:
  <name>:
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:<port>/readyz').status==200 else 1)"]
      interval: 30s
      start_interval: 2s     # Docker 25+ — polls every 2s during start_period
      start_period: 60s      # grype DB download grace, etc.
      timeout: 10s
      retries: 3
```

(Docker Hardened Images without a shell → use the sidecar + `network_mode: service:<name>` pattern instead. Not needed today; `python:3.12-slim` ships `python3`.)

## `depends_on: service_healthy` — compose startup ordering

Dependents **must** wait for `service_healthy`, not `service_started`. Scout starting before the hub's grype DB downloads caused a race in BC-005 that was papered over with `MIN_CVSS` plus restart-on-failure; this is the right fix.

```yaml
scout:
  depends_on:
    hub:
      condition: service_healthy
```

## Scout analogue (loop services with no HTTP by default)

Scout and future loop-only services may use file-sentinels in `/data/` instead of an HTTP server:

- `/livez` → `pgrep -f scout.py` and `/data/scout.live` mtime < `SCAN_INTERVAL + 60s`
- `/readyz` → `/data/scout.ready` exists (written after Docker socket probe succeeds)
- `/startupz` → `/data/scout.started` exists (written after first full inventory POSTed)
- `/healthz` → read `/data/scout.healthz.json` (written each loop with last-scan metrics)

HTTP preferred for anything with an existing HTTP server; file-sentinel acceptable for pure-loop services.

## Checklist — adding a new beaconry container

Before PR'ing a new service (BC-012 made step 1 the smallest work and gate-protected):

- [ ] **`COPY beaconry_health/ ./beaconry_health/`** in your Dockerfile. The lint (`make check` → `verify-dockerfile-health-copy`) fails if you forget this and your service `import`s from the package.
- [ ] Instantiate / configure, don't reimplement: hub-shaped service → `api.include_router(build_router(my_livez, my_readyz_providers, my_startup_flag_getter, my_healthz_summary))`. Loop-only service → `writer = SentinelWriter(DATA_DIR); writer.live()` etc. The restart-loop trap is enforced by `build_router`'s types — passing a dep-checking probe to the `/livez` slot is a pyright error.
- [ ] Compose service has a healthcheck pointed at `/readyz` (HTTP) or the sentinel-file test (loop services).
- [ ] Any service that depends on this one declares `condition: service_healthy`.
- [ ] `make status` includes the new service in its one-line summary.
- [ ] The new container's Dockerfile ships whatever its healthcheck script needs (python / curl / shell — see `memory/docker-standard.md`).

## Design pointers

- **Convention origin**: Kubernetes `/healthz`; the `z` suffix exists to avoid collision with pre-existing `/health` handlers.
- **`/livez` vs `/readyz` split**: cost of conflating them is restart-loops when a dependency breaks. Non-negotiable.
- **Future k8s port**: endpoints match k8s probe conventions 1:1. When we move, probe wiring is config, not code.
- Related memory: `memory/docker-standard.md § Healthcheck patterns`, `memory/design-decisions.md § Auto-fix trust model` (BC-011 waterfall shares this vocabulary for target containers).
