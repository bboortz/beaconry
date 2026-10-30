# BC-012 — Shared `beaconry_health/` package

*Branch: `feat/BC-012-shared-beaconry-health`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-012-shared-beaconry-health.md`*

---

## Situation

BC-010 shipped the four-endpoint convention and demonstrated it on hub (inline endpoints in `hub.py`) and scout (`scout/health.py` with sentinel writers). The convention is one; the implementation is two shapes. `find . -name health.py` returns only `scout/health.py`. A hypothetical third container would re-read the memory doc and reimplement. The restart-loop trap — "do not check external deps in `/livez`" — is a memory rule, enforced only by careful review.

---

## Problems to solve

- Hub endpoints inline in `hub.py` (~120 lines); scout writers in `scout/health.py`. Two implementations of one convention
- `_check_db / _check_grype_db / _check_grype_binary / _check_telegram` in hub are hub-specific but duplicate generic shapes (file-exists probe, binary-on-PATH probe, external-service-reachable probe) that other containers will need
- The restart-loop trap is a *rule* in `memory/health-checks.md`, not a *constraint* in code. Future author imports `check_db` into their `/livez` provider and nothing stops them
- No gate detects a container Dockerfile that imports from a shared health module but forgets the matching `COPY`

---

## Goals

1. New package `poc/beaconry_health/` with `endpoints.py`, `sentinels.py`, `checks.py`, `trap.py`
2. Hub endpoints become `api.include_router(build_router(hub_check_providers))` — behavior identical, 1 line in `hub.py` replaces ~120 lines
3. Scout's current `scout/health.py` becomes a thin config module naming scout-specific checks; sentinel writer plumbing moves to `beaconry_health.sentinels`
4. **Typed trap guard**: `LivezProvider = Callable[[], Literal["alive"]]` vs `ReadyzProvider = Callable[[], dict[str, str]]` — distinct types; pyright rejects passing a `ReadyzProvider` to the `/livez` slot
5. Dockerfile lint test (`make check` enforced): any container Dockerfile that imports / exec's `beaconry_health` must also `COPY beaconry_health/`
6. `memory/health-checks.md § Checklist` leads with the COPY step
7. All existing BC-010 tests pass unchanged (behavior-preserving refactor)
8. 1 business CAP + 3 technical CAPs registered

---

## Scope

- `poc/beaconry_health/` — new package (5 files)
  - `__init__.py` — re-exports: `build_router`, `SentinelWriter`, `check_db`, `check_binary`, `check_dir_populated`, `LivezProvider`, `ReadyzProvider`
  - `endpoints.py` — `build_router(livez_provider, readyz_providers, startup_flag_getter, healthz_summary_getter, version) -> APIRouter`
  - `sentinels.py` — `SentinelWriter(data_dir)` class with `.live() .ready() .started() .healthz(**summary)`
  - `checks.py` — reusable probes:
    - `check_db(path: Path) -> str` — "ok" or "unavailable"
    - `check_binary(name: str) -> str` — "ok" or "missing"
    - `check_dir_populated(path: Path) -> str` — "ok", "missing", "empty"
  - `trap.py` — typed stubs:
    - `LivezProvider = Callable[[], Literal["alive"]]`
    - `ReadyzProvider = Callable[[], dict[str, str]]`
    - `HealthzSummaryProvider = Callable[[], dict[str, Any]]`
- `poc/hub/hub.py` — delete ~120 lines of inline endpoints + `_check_*`. Add:
  - `from beaconry_health import build_router, check_db, check_binary, check_dir_populated`
  - Define `_hub_readyz_checks()` returning `{"db": ..., "grype_db": ..., "grype_binary": ...}`
  - Define `_hub_healthz_summary()` returning full dict with Telegram + last_scan_ts + version
  - `api.include_router(build_router(livez_provider=lambda: "alive", readyz_providers=_hub_readyz_checks, ...))`
- `poc/hub/Dockerfile` — add `COPY beaconry_health/ ./beaconry_health/` next to `COPY hub.py .`
- `poc/scout/scout.py` — replace `import health as scout_health` with `from beaconry_health import SentinelWriter`; use `writer = SentinelWriter(DATA_DIR); writer.live()` etc.
- `poc/scout/health.py` — becomes a thin ScoutChecks config module (or delete entirely if everything moves to `beaconry_health`)
- `poc/scout/Dockerfile` — add `COPY beaconry_health/ ./beaconry_health/`
- `poc/scripts/verify_traceability.py` or new `poc/scripts/verify_dockerfile_health_copy.py` — Dockerfile-COPY lint rule
- `Makefile` — hook the new lint into `make check`
- `memory/health-checks.md` — update § Checklist to lead with COPY step; add note that endpoints come from `build_router`, not hand-written
- `docs/CAPABILITIES.md` — register CAP-B016, CAP-T040/T041/T042
- Tests:
  - `poc/tests/unit/test_beaconry_health_endpoints.py` — router factory shape, 503 reasons, JSON summary shape
  - `poc/tests/unit/test_beaconry_health_sentinels.py` — SentinelWriter class
  - `poc/tests/unit/test_beaconry_health_checks.py` — probe primitives
  - `poc/tests/unit/test_beaconry_health_trap.py` — compile-time trap guard (pyright or structural check)
  - `poc/tests/integration/test_BC_012_dockerfile_copy_lint.py` — Dockerfile lint rule
  - Existing `test_hub_health.py` + `test_scout_health.py` + `test_status_cli.py` continue to pass unchanged

---

## Non-goals

- Scout live HTTP endpoints (BC-013)
- Target-container health probing (BC-011)
- Replacing BC-010 behavior — strict preservation
- Switching to Pydantic for health responses — stdlib dicts fine for POC
- Making `beaconry_health` pip-installable — stays as a `COPY`-able source tree for now

---

## Design decisions

**Package lives at `poc/beaconry_health/`, not `poc/hub/health/`.** Rejected: house it under hub. Reason: a package under one service implies ownership; placing it at the POC root signals it's shared infrastructure. Both `hub/Dockerfile` and `scout/Dockerfile` COPY from the same place (`../beaconry_health`).

**Typed trap via distinct `Callable` aliases, not Protocols.** Rejected: `class LivezProvider(Protocol): ...` with method signatures. Reason: our providers are plain callables, not stateful objects. Callable aliases + Literal return types pyright-reject the misuse without protocol runtime machinery.

**`build_router` returns an `APIRouter`, not registering on an `app` directly.** Rejected: `register_endpoints(app, ...)`. Reason: APIRouter is testable in isolation (TestClient(router)), composable (prefix mount), and FastAPI-idiomatic.

**Dockerfile lint rule: AST-walk the Dockerfile for `COPY beaconry_health`; cross-check with `grep -r beaconry_health poc/<service>/*.py`.** Rejected: skip the lint, trust review. Reason: forgetting the COPY is a container-start failure in production, not a test failure in CI. Catch at merge time via gate, not at `docker compose up` time.

**Behavior-preserving refactor.** Rejected: expand surface area while refactoring. Reason: BC-010 was shipped yesterday; changing behavior mid-flight makes bisecting post-ship regressions painful. New behavior lives in BC-011 / BC-013.

**Delete `poc/scout/health.py`** (not keep it as a config shim). Rejected: keep it with 3 lines calling into beaconry_health. Reason: trivial indirection; `SentinelWriter(DATA_DIR)` at scout.py top-level is clearer. Scout-specific config (if any emerges in BC-013) can live inline in `scout.py`.

---

## Breaking changes

- `poc/hub/hub.py` loses ~120 lines of inline endpoint code — importers (if any outside tests) relying on `hub._check_db` etc. break. **Only internal use today**: tests reference these; tests get updated to import from `beaconry_health.checks`. Not a public API change.
- `poc/scout/health.py` deleted. **Only internal consumer is scout.py**, migrated as part of this refactor.
- Hub + scout Dockerfiles grow one `COPY` line each. Rebuild required on this branch (as with BC-009's docker CLI add and BC-010's scout health.py add).
- Nothing operator-visible changes. `make status`, `make demo`, `/healthz`, `/livez` responses — all byte-identical to BC-010.

---

## Merge plan

1. Phase 1: branch + this plan + story in-progress (DONE)
2. Phase 2: red unit tests for the new package (endpoints router, sentinels, checks, trap guard)
3. Phase 3: implementation — package files + hub refactor + scout refactor + Dockerfile updates
4. Phase 4: Dockerfile lint rule; CAPs registered; all gates green; Shipping evidence filled
5. Phase 5: `docker compose up` live-verify — hub + scout behave identically to BC-010; `make status` unchanged; grype DB simulated loss still shows restart-loop trap behavior; story → shipped; fast-forward to `dev`

---

## Rollout plan

Purely additive from an operator standpoint. From a developer standpoint:
- Future container implementers follow the updated checklist in `memory/health-checks.md`
- `poc/beaconry_health/` is the new mental model; existing contributors should read the updated memory file once
- Dockerfile lint will initially flag only one potential error (hub or scout forgetting the COPY) — easy to spot in local dev

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Hub mounts endpoints via build_router — behavior unchanged | unit test | test_hub_health.py passes unchanged | ✓ |
| Scout uses SentinelWriter — behavior unchanged | unit test | test_scout_health.py passes unchanged | ✓ |
| Typed trap guard rejects dep probes in /livez slot | integration test | `test_BC_012_trap_pyright_enforcement.py::test_pyright_rejects_readyz_shaped_fn_in_livez_slot` | ✓ |
| Dockerfile lint catches forgotten COPY | integration test | `test_BC_012_dockerfile_copy_lint.py` | ✓ |
| Refactor is behavior-preserving — compose up unchanged | live manual | 2026-10-30 — cold `docker compose up` triggers same chain (`beaconry-hub Healthy` → `beaconry-scout Started`), `make status` prints identical shape to BC-010, `rm -rf /data/grype-db` → `/readyz` 503, `/livez` 200, `make status` → "hub: degraded (failing: grype_db...)" exit 2, hub RestartCount 0→0. Also caught two regressions on first live-verify attempt: scout container ImportError on fastapi (fixed by graceful endpoints import in `__init__.py`); BC-005 compose test used old build context (fixed to use widened poc/ context + -f). Both land in this commit. | ✓ |
| New-container checklist leads with COPY step | self-consistency | memory/health-checks.md diff | ✓ |
| `beaconry_health.endpoints.build_router` round-trips 4 endpoints | unit test | `test_beaconry_health_endpoints.py::test_router_exposes_four_endpoints` | ✓ |
| `beaconry_health.sentinels.SentinelWriter` wraps the four writers | unit test | `test_beaconry_health_sentinels.py` suite | ✓ |
| Reusable probes return "ok"/"missing"/"unavailable" | unit test | `test_beaconry_health_checks.py` suite | ✓ |
| CAP-B016 + CAP-T040/T041/T042 registered | verify-traceability | `make check` traceability green | ✓ |
| Hub inline health code removed — ~120 LOC net reduction | diff metric | `git diff --stat` on hub.py shows net negative | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

**Unit** (fast tier):
- `test_beaconry_health_endpoints.py` — router factory with mocked providers; four endpoints respond with expected shapes; 503 for failing readyz
- `test_beaconry_health_sentinels.py` — SentinelWriter class; the BC-010 scout tests become redundant and can be deleted or kept thin
- `test_beaconry_health_checks.py` — `check_db` / `check_binary` / `check_dir_populated` round-trip tests with `tmp_path`
- `test_beaconry_health_trap.py` — the compile-time guard. If testable at runtime (e.g. `typing.get_type_hints`), do that; else use a pytest-marked test that invokes pyright on a fixture file and asserts a specific error line
- Existing `test_hub_health.py` / `test_scout_health.py` **must pass unchanged** — this is the non-regression property

**Integration** (full tier):
- `test_BC_012_dockerfile_copy_lint.py` — fixture Dockerfile that imports `beaconry_health` but omits COPY; invoke the lint; assert specific error

**Live** (Phase 5):
- `docker compose down -v && docker compose up` — hub `/readyz` goes healthy after grype DB download; scout released; `make status` prints identical shape to BC-010; `rm -rf /data/grype-db` → degraded, no restart loop

---

## Testing commands

```bash
source .venv/bin/activate
make check                                              # fast tier incl. new lint
make check-full                                         # full tier (compose + real_tools)
pytest poc/tests/unit/test_beaconry_health*.py -v       # the new package
pytest poc/tests/unit/test_hub_health.py -v             # non-regression
pytest poc/tests/unit/test_scout_health.py -v           # non-regression
pyright poc/beaconry_health/                            # type-check the trap stubs
cd poc && docker compose up                             # Phase 5 live
make status                                             # expect: hub: healthy ... · scout: healthy ...
```
