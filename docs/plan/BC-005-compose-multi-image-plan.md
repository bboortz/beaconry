# BC-005 — Multi-image Compose demo

*Branch: `feat/BC-005-compose-multi-image`*
*Start date: 2026-10-29*
*Story: `docs/stories/BC-005-compose-multi-image.md`*

---

## Situation

BC-002 proved the real pipeline works for one hard-coded image, run via local Python. The homelab operator who wants to show beaconry to a friend needs the real demo to be *zero-setup* on the host: Docker + Compose only, no Python toolchain, no syft/grype install. The existing `poc/docker-compose.yml` + `poc/scout/scout.py` + `poc/hub/hub.py` from BC-000 were designed for this but never actually run end-to-end.

Additionally, scanning the host's *entire* Docker inventory can produce a flood even with BC-002's per-run cap. A conservative allowlist default (`nginx*, redis*, postgres*`) keeps the demo honest without being spammy.

---

## Problems to solve

- `make demo` currently requires local Python + syft/grype binaries → not demo-ready for someone with just Docker
- Scout today scans *every* image on the host → noisy, no way to target a demo subset
- The existing Compose stack has never been exercised end-to-end — rot risk
- `scout` mount of `/var/run/docker.sock` must be `:ro` (security operator critique) — verify current compose config
- Operator with zero matching images sees silence; need a fail-friendly log line

---

## Goals

1. `make demo` without `BEACONRY_MOCK=1` runs `cd poc && docker compose up --build`
2. `BEACONRY_MOCK=1 make demo` still runs the local-Python mock pipeline unchanged
3. Scout honors `SCOUT_INCLUDE` and `SCOUT_EXCLUDE` glob env vars; default include is `nginx*,redis*,postgres*`; default exclude is empty
4. Scout logs include which images were scanned and which were skipped (and why — matched exclude vs. didn't match include)
5. Scout logs "0 image(s) matched SCOUT_INCLUDE" with a glob-adjustment hint when the filter produces an empty set
6. Scout's docker-socket mount in `poc/docker-compose.yml` is `:ro` (verify + fix if not)
7. The whole stack boots end-to-end locally against the real Telegram bot (manual verification)
8. New capabilities registered in `docs/CAPABILITIES.md` (CAP-B009, CAP-B010, CAP-T018, CAP-T019)
9. Integration test with opt-in `compose` marker (skipped by default) that spins up the stack with Telegram mocked and asserts a Finding is saved
10. README updated to document the Compose entry point

---

## Scope

- `poc/scout/scout.py` — new `filter_image_tags(tags, include_globs, exclude_globs)` pure function; `scan_docker_images` uses it; env var parsing for `SCOUT_INCLUDE`/`SCOUT_EXCLUDE` (comma-separated glob list); informative log lines
- `poc/.env.example` — document `SCOUT_INCLUDE` + `SCOUT_EXCLUDE` with the demo-safe default
- `poc/docker-compose.yml` — verify scout docker-socket mount is `:ro`; propagate `SCOUT_INCLUDE`/`SCOUT_EXCLUDE` env; propagate `BEACONRY_MAX_FINDINGS_PER_RUN`
- `Makefile` — `demo` target branches on `BEACONRY_MOCK`:
  - `BEACONRY_MOCK=1` → existing `$(PYTHON) -m poc.demo $(ARGS)`
  - else → `cd poc && docker compose up --build $(COMPOSE_ARGS)`
- `poc/tests/unit/test_scout_filter.py` — unit tests for `filter_image_tags`
- `poc/tests/integration/test_BC_005_compose.py` — opt-in `compose` marker; skips if `docker compose` unavailable; builds, starts, asserts, tears down
- `pyproject.toml` — register `compose` marker; default addopts exclude it like `real_tools`
- `docs/CAPABILITIES.md` — new CAPs (B009, B010, T018, T019); update CAP-B002 and CAP-B004 origin to include BC-005
- `docs/stories/INDEX.md` — BC-005 shipped row at the end
- `README.md` — new "Demo via Compose" section explaining `make demo` now boots containers

---

## Non-goals

- Auto-fix execution via Compose (BC-003 territory)
- Verifying buttons work live under Compose (BC-003 territory; same callback code)
- Compose + ARM / Pi
- Multi-host orchestration
- Scanning non-Docker inventory in the Compose demo (pacman is dev-only)
- Changing scout's `collect_and_post` structure or hub's receive_scan structure
- Automating the first-time Docker + Compose install
- A mocked-Compose unit test (compose marker test is enough)

---

## Design decisions

**`make demo` dispatches on `BEACONRY_MOCK` env.** User's directive. Make target uses a shell `if` so a single UX ("just type `make demo`") covers both modes. Rejected alternative: two targets (`make demo` + `make demo-compose`) — doubles the surface, operator has to pick.

**Default include glob is `nginx*,redis*,postgres*`.** Narrow enough to be demo-safe (no accidental surprise), broad enough that most homelab operators have at least one match. User chose this at clarification time.

**Scout filter is a pure function** (`filter_image_tags(tags, include_globs, exclude_globs) -> list[str]`). Keeps unit tests fast + deterministic; `scan_docker_images` just wires `docker.from_env()` to the filter output.

**Glob syntax = `fnmatch` style** (shell globs: `*`, `?`, `[seq]`). Standard library, no new deps. Operators recognize `nginx*` syntax from the shell.

**Scout docker socket is `:ro`.** Scout never pulls or tags — those are hub's job via DockerAdapter. Enforced in `poc/docker-compose.yml`; verified by the compose-marker integration test if we assert the mount config.

**Compose marker integration test opts in** (via `pytest -m compose`). Default `make check` skips it (same pattern as `real_tools`). This is correct for CI because:
- A full compose-up takes ~5 min on cold cache
- Requires docker daemon available
- Our pre-push doesn't need it to pass; it's a periodic validation

**0-matches is a loud warning, not an error.** Operator with an unusual stack should see guidance to adjust `SCOUT_INCLUDE`, not a crash.

**Rejected: a separate `docker-compose.mock.yml` profile.** The mock path is in-process Python; Compose for mock is overkill. Mock stays local.

**Rejected: configurable allowlist via Compose profile.** Env vars are enough.

**Rejected: making scout optionally stay-alive after one scan.** Scout already polls on `SCAN_INTERVAL`. For the demo, operator can `docker compose down` when satisfied.

---

## Breaking changes

- `make demo` now boots Docker containers by default. Operators who were running `make demo` without `.env` and without a mock env flag would hit a prerequisite error (compose exits early on missing env). Mitigation: `make demo` without a complete `.env` AND without `BEACONRY_MOCK=1` emits a helpful error that names both options.
- The `docker-compose.yml` env list grows (`SCOUT_INCLUDE`, `SCOUT_EXCLUDE`, `BEACONRY_MAX_FINDINGS_PER_RUN`). Backwards compatible — missing env resolves to defaults.
- Scout's default scanning scope changes from "all images" to "matching nginx*,redis*,postgres*". Any existing operator relying on "scan everything" needs to set `SCOUT_INCLUDE=*`.

---

## Merge plan

1. All work on `feat/BC-005-compose-multi-image`
2. **Red first:** unit tests for `filter_image_tags` + a compose-marker integration test (skipped by default)
3. **Green:** implement filter + env parsing + Makefile dispatch; verify Compose stack boots end-to-end against real Telegram
4. **Register capabilities** in `docs/CAPABILITIES.md`; update test files' `Covers:` blocks; `make verify-traceability` passes
5. **Manual verification** together: `make demo` with real `.env`, operator sees matching images scanned, Telegram message arrives with real cross-image CVEs
6. `make check` passes locally
7. Flip story → `shipped`, update INDEX
8. Fast-forward merge to `dev`
9. Delete branch

---

## Rollout plan

Additive — no runtime migration. First-time Compose-demo setup for any clone:

```bash
# One-time
cp poc/.env.example poc/.env   # fill TELEGRAM_BOT_TOKEN + CHAT_ID
docker pull nginx:1.24         # or any image matching the default allowlist

# Each run
make demo                      # real pipeline via Compose
BEACONRY_MOCK=1 make demo      # mock, no Telegram
docker compose -f poc/docker-compose.yml down   # teardown
```

---

## Testing strategy

**Unit tests** (`poc/tests/unit/test_scout_filter.py`):
- Empty tags → empty result
- Include=`["*"]`, exclude empty → all tags pass through
- Include=`["nginx*"]` → only matching tags
- Include=`["*"]`, exclude=`["*-test"]` → excludes `-test` suffix
- Include takes priority: `["nginx*"]` + exclude=`["nginx-dev*"]` → nginx-dev* excluded
- Default allowlist (`nginx*,redis*,postgres*`) with mixed inventory → correct subset

**Integration test** (`poc/tests/integration/test_BC_005_compose.py`, marker `compose`):
- Skip unless `shutil.which("docker")` and `docker compose version` succeeds
- Build and `up -d` the stack with Telegram monkey-patched at the hub module (via build-arg or env flag that forces `BEACONRY_MOCK=1` inside the containers for THIS test — rejected because that defeats the purpose)
- Alternative: real Compose + a stub Telegram server (overkill for BC-005)
- Final choice: compose integration test uses `docker compose config` to assert env wiring + `:ro` socket mount, plus a quick `docker compose up -d && curl :8000/health && docker compose down` to prove the hub binds and health responds. End-to-end CVE assertion deferred to manual verification.

**Manual verification** (together with user):
1. `cp poc/.env.example poc/.env`, fill Telegram creds (already in local `.env`)
2. `make demo`
3. Observe: compose builds, scout + hub both reach healthy
4. Observe: scout logs name the matching images (nginx:1.24 at minimum)
5. Observe: Telegram message(s) arrive on operator's phone
6. `docker compose -f poc/docker-compose.yml down`
7. Observe: containers gone; `docker volume ls | grep beaconry-data` shows the volume survives

---

## Testing commands

```bash
source .venv/bin/activate

# Full gate (mock + unit tests; compose + real_tools opt-in)
make check

# Opt-in compose test
pytest -m compose -v

# Real demo via Compose
make demo

# Mock demo (unchanged BC-002 path)
BEACONRY_MOCK=1 make demo
```

Expected: `make check` exits 0; `make demo` boots the Compose stack and real Telegram messages arrive; `BEACONRY_MOCK=1 make demo` runs the local Python mock as before.
