---
id: BC-099
title: Fail the build if builder Python minor ≠ runtime Python minor (BuildKit required)
status: shipped
created: 2026-11-08
branch: feat/BC-099-base-abi-guard
plan: docs/plan/BC-099-base-abi-guard-plan.md
---

# BC-099: Build-time ABI guard — reject site-packages into a sys.path the runtime won't scan

## Narrative

BC-098 shipped a Python-minor-agnostic staging pattern:

```dockerfile
PY_VER="$(python3 -c '…')" && mv /install/lib/python${PY_VER}/... /stage/usr/local/lib/python${PY_VER}/...
```

The `PY_VER` here comes from the **builder stage's** Python
interpreter (`forge-builder-trixie:latest`). BC-098 trusted that
forge's two bases (`forge-builder-trixie` + `python-scratch-trixie`)
would always carry the same Python minor. Forge FG-032's story called
them "bumped as a pair" — they are, but **not atomically**.

On 2026-11-08 the published forge timestamps were:

- `forge-builder-trixie:latest` → rebuilt 2026-11-08 17:10:53 UTC (Python 3.14)
- `python-scratch-trixie:latest` → rebuilt 2026-11-08 17:15:57 UTC (Python 3.14)

Five minutes apart. During that window, `forge-builder-trixie:latest`
was Python 3.14 while `python-scratch-trixie:latest` was still Python
3.13. Forge's `beaconry-hub` build job fired inside that window.
Result: the published `ghcr.io/bboortz/beaconry-hub:latest` ships with
wheels at `/usr/local/lib/python3.14/site-packages/` and a runtime
Python 3.13 interpreter whose sys.path points at
`/usr/local/lib/python3.13/site-packages/` (empty).

Operator symptom on hetzner + server2 after `make deploy-beaconry`:

```
ModuleNotFoundError: No module named 'uvicorn'
Traceback (most recent call last):
  File "/app/hub.py", line 22, in <module>
    import uvicorn
```

The two python directories are present side-by-side inside the image:

```
usr/local/lib/python3.13/
usr/local/lib/python3.13/site-packages/           ← empty, runtime looks here
usr/local/lib/python3.14/
usr/local/lib/python3.14/site-packages/fastapi/   ← wheels, runtime can't see
```

BC-099 adds a build-time ABI guard that **fails the build loudly**
rather than producing an image like this. If forge's bases ever drift
minors again, the guard trips, the build fails with a clear message,
and no broken image reaches GHCR — operators never see the import error.

## Trigger

Operator-visible: `ghcr.io/bboortz/beaconry-hub:latest` pulled on
hetzner + server2 (2026-11-08 17:26 UTC) crash-looped with
`ModuleNotFoundError: No module named 'uvicorn'`. Root cause:
split-brain forge publish race during FG-032's in-place Python 3.14
bump.

Pattern-visible: BC-098's `PY_VER` is read from one image (builder)
while the runtime enforces its own `PY_VER` via sys.path. These must
match at build time for imports to resolve; nothing today asserts
they do.

## Expected outcome

- A new build-time step in **both** `poc/{hub,scout}/Dockerfile`
  probes the runtime base's Python minor (via BuildKit
  `RUN --mount=from=<runtime-image>`) and compares it to the
  builder's `PY_VER`. If they differ, the build exits non-zero with
  a self-explanatory message naming both minors and pointing at this
  story.
- `make live-rebuild` + the full cold-start stack smoke green against
  today's (now-consistent) forge bases.
- `test_hub_can_import_all_runtime_deps` + `test_scout_can_import_all_runtime_deps`
  (BC-083) still pass — same oracle BC-098 used.
- New unit test `test_BC_099_base_abi_guard.py` asserts the probe
  exists in both Dockerfiles and emits the right error when minors
  disagree (regex check on a synthetic Dockerfile fixture).
- **Operator remedy for the shipped-broken image is NOT in this
  story's scope** — forge rebuilt both bases since the race window
  closed, so forge's next scheduled `beaconry-hub` build will
  publish a working image automatically. Operators can
  `make deploy-beaconry` to repull once that lands. Documented in
  the plan's Rollout.

## Out of scope

- **Fixing forge's publish atomicity.** The root cause lives in
  forge (FG-032's rebuild job should block on both bases being
  consistent before publishing them). Opening a separate FG-NNN
  ask. BC-099 is defense-in-depth on beaconry's side regardless.
- **Supporting intentional minor-drift between builder + runtime.**
  Python C-extension wheels (pydantic_core, orjson, …) are ABI-bound
  to the exact minor; running 3.14-built wheels on 3.13 runtime
  would fail at `import` time regardless of path. The right answer
  is "fail the build" — the guard enforces exactly this.
- **Rebuilding + republishing beaconry images from this branch.**
  Forge owns that. BC-099 ships the guard; forge's next cycle
  rebuilds with it.
- **Retrying the build automatically.** If the guard trips, the
  build fails — operator (or forge CI) investigates. No auto-retry
  logic, no polling. Loud failure > silent recovery for a
  supply-chain race.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Beaconry builds refuse to produce an image with ABI-mismatched Python

  Scenario: Builder + runtime Python minors match → build succeeds
    Given a pull of forge-builder-trixie:latest at Python 3.X
      And a pull of python-scratch-trixie:latest at the same 3.X
    When `docker build` runs on poc/hub/Dockerfile
    Then the build succeeds
     And the image contains /usr/local/lib/python3.X/site-packages/<deps>
     And `python3 -c 'import uvicorn'` inside the container exits 0

  Scenario: Builder + runtime Python minors differ → build fails
    Given a pull of forge-builder-trixie:latest at Python 3.Y
      And a pull of python-scratch-trixie:latest at a different Python 3.Z
    When `docker build` runs on poc/hub/Dockerfile
    Then the build exits non-zero
     And stderr names both minors (3.Y, 3.Z)
     And stderr points at BC-099 as the story explaining the invariant
     And NO partial image is published

  Scenario: Scout mirrors hub's guard
    Same two scenarios for poc/scout/Dockerfile.

  Scenario: Guard survives Dockerfile refactoring
    Given a unit test fixture that synthesizes a drifted Dockerfile
    When the lint runs on the real poc/{hub,scout}/Dockerfile
    Then both files contain the ABI guard RUN step
     And the guard invokes python3 on the runtime image via `--mount=from=`
```

## Capabilities delivered

- **CAP-T201: Build-time ABI guard against base-image Python-minor
  drift** (type: technical, status: active). Builder stage probes
  the runtime base's Python minor via BuildKit mount and fails the
  build iff it differs from the builder's. Backstop for any future
  forge publish race during a Python minor bump.

Reference existing CAPs:
- Extends **CAP-T200** (BC-098's stage-at-runtime-path). BC-098
  trusted bases to match; BC-099 verifies they do.
- Supports CAP-T171 (base-image preflight) and CAP-B023 (operator
  trust in the supply chain).

## Resolved

1. **BuildKit required** — option (a) chosen. Both Dockerfiles gain
   `# syntax=docker/dockerfile:1` directive (forces BuildKit) and
   the Makefile exports `DOCKER_BUILDKIT=1` + `COMPOSE_DOCKER_CLI_BUILD=1`
   so `docker compose build` / `docker build` paths both go through
   the new builder. All Docker ≥ 23 installs default to BuildKit via
   `buildx`, so no operator action required.
2. **Guard failure message** — final wording in the plan. Names both
   minors, points at BC-099, documents the remedy (`docker pull`
   both bases fresh + rebuild).

## Links

- Forge FG-032 (origin of the publish race): `~/git/forge/docs/stories/FG-032-python-3.14-bump.md`
- Forge PR #60: `https://github.com/bboortz/forge/pull/60`
- BC-098 (pattern this story defends): `docs/stories/BC-098-python-version-detect.md`
- BC-083 (`test_{hub,scout}_can_import_all_runtime_deps` — oracle)
- Observable incident: 2026-11-08 ~17:26 UTC, hetzner + server2,
  `ModuleNotFoundError: No module named 'uvicorn'` on hub startup.
- Diagnostic command that confirmed root cause:
  `docker export $(docker create ghcr.io/bboortz/beaconry-hub:latest noop) | tar -tf - | grep usr/local/lib/python3`
  → shows both `/python3.13/` + `/python3.14/site-packages/fastapi`
  co-existing in the same image.
