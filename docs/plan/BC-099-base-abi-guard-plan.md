# BC-099 — Build-time ABI guard: reject site-packages into a sys.path the runtime won't scan

*Branch: `feat/BC-099-base-abi-guard`*
*Start: 2026-11-08*
*Story: `docs/stories/BC-099-base-abi-guard.md`*

---

## Situation

2026-11-08 ~17:26 UTC: hetzner + server2 pulled fresh
`ghcr.io/bboortz/beaconry-hub:latest` and crashed on startup with
`ModuleNotFoundError: No module named 'uvicorn'`. Live diagnosis:

```
$ docker export $(docker create ghcr.io/bboortz/beaconry-hub:latest noop) \
    | tar -tf - | grep usr/local/lib/python3
usr/local/lib/python3.13/
usr/local/lib/python3.13/site-packages/           ← empty (runtime sys.path)
usr/local/lib/python3.14/
usr/local/lib/python3.14/site-packages/fastapi/   ← wheels (unreachable)
```

Two Python directories in the same image. The runtime base shipped
with Python 3.13 (sys.path → `/usr/local/lib/python3.13/site-packages`
empty), the builder stage had already rebuilt against Python 3.14
(wheels landed at `/usr/local/lib/python3.14/site-packages`). Forge
timestamps: `forge-builder-trixie:latest` rebuilt 2026-11-08
17:10:53 UTC, `python-scratch-trixie:latest` rebuilt 17:15:57 UTC —
a **5-minute publish race** during FG-032's in-place Python bump.

BC-098 trusted the two bases to match. They do — eventually. Not
atomically. During the gap, a beaconry image can be built with
mismatched bases and still produce a syntactically valid image. Our
only current guard is the `test_hub_can_import_all_runtime_deps`
cold-start test, which fires in CI long after the build has
produced the artifact.

---

## Problems to solve

- Builds complete during a forge publish race produce broken images
  with no build-time signal.
- Operators see the failure as a crash-loop on deploy, not at build
  time where remediation (wait + rebuild) is obvious.
- Nothing currently enforces the ABI invariant BC-098 requires.

---

## Goals

1. **G1 — Detect the ABI mismatch at build time.** Both Dockerfile
   builder stages probe the runtime base's Python minor via BuildKit
   `--mount=from=<runtime-src>` and compare with the builder's.
2. **G2 — Fail loudly on mismatch.** Non-zero exit with a stderr
   message naming both minors, pointing at BC-099, and documenting
   the remedy (wait for forge next rebuild + repull both bases +
   rebuild). No partial image published.
3. **G3 — Single-version guarantee.** When the guard passes, only
   one `/usr/local/lib/python3.X/` directory exists in the final
   runtime image — by construction, because the builder's mv target
   and the runtime base's stdlib directory have the same minor.
   Defense-in-depth: also assert the runtime base contains exactly
   one `python3.X` directory before reading its minor (hedges
   against a hypothetical forge base-image bug).
4. **G4 — Enforce BuildKit globally.** `# syntax=docker/dockerfile:1`
   directive at the top of both Dockerfiles makes classic builder
   reject the file with a clear error. Makefile exports
   `DOCKER_BUILDKIT=1` + `COMPOSE_DOCKER_CLI_BUILD=1` so compose and
   plain `docker build` invocations both route through BuildKit.
5. **G5 — Live oracle still passes.** `make check-full`'s cold-start
   import probes (BC-083) remain green against today's (now-consistent)
   forge bases.
6. **G6 — CAP-T201 registered.**

---

## Scope

### Modified files

- `poc/hub/Dockerfile`:
  - Add `# syntax=docker/dockerfile:1` at the top.
  - Add a new named stage `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src`
    between the existing grype-src stage (line 11) and the builder
    stage (line 13 current). Purpose: a stable named reference for
    the `--mount=from=` step; ensures BuildKit resolves the probe
    + the runtime FROM to the same digest within one build.
  - Replace BC-098's `RUN PY_VER=… && mv …` step (after the
    `mkdir -p /stage/data` line) with a combined RUN that mounts
    `runtime-src`'s `/usr/local/lib`, reads the runtime minor,
    guards equality with builder's, and does the BC-098 mv on
    success.
- `poc/scout/Dockerfile`:
  - Add `# syntax=docker/dockerfile:1` at the top.
  - Add `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src`
    before the builder FROM.
  - Replace BC-098's PY_VER + mv step with the combined guard + mv
    step (same shape as hub).
- `Makefile`:
  - `export DOCKER_BUILDKIT = 1` + `export COMPOSE_DOCKER_CLI_BUILD = 1`
    at the top (right after .PHONY). Both propagate to every make-invoked
    `docker` and `docker compose` subshell.
- `docs/CAPABILITIES.md`:
  - Add CAP-T201 entry for the ABI guard.
  - Update CAP-T200's description to note BC-099 added the guard
    (one-line forward reference; CAP-T200 stays active + authoritative
    for the staging pattern).
- `docs/stories/INDEX.md` — BC-099 in-progress row (already applied
  during story draft).
- `CHANGELOG.md` — v0.16.7 entry.
- `VERSION` + `poc/VERSION` + `pyproject.toml` — 0.16.6 → 0.16.7.

### Added files

- `poc/tests/unit/test_BC_099_base_abi_guard.py` — grep-based lint:
  - `# syntax=docker/dockerfile:` directive at the top of both files.
  - `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src`
    present in both.
  - Builder has exactly one `RUN --mount=…from=runtime-src,…` step
    that:
    - Reads `BUILDER_PY_VER` from `python3 -c 'import sys; …'`.
    - Reads `RUNTIME_PY_VER` from the runtime base's `/usr/local/lib`
      directory name.
    - Compares the two and exits non-zero on mismatch.
    - Carries the `BC-099 ABI guard tripped` marker string.
  - No BC-098-shape PY_VER RUN remains (the step is subsumed, not
    duplicated).
  - Makefile has `export DOCKER_BUILDKIT = 1` + `export COMPOSE_DOCKER_CLI_BUILD = 1`.

### Not modified

- `poc/scripts/verify_pinned_versions.py` — base tag acceptance is
  the same (`:latest`).
- `poc/scripts/dogfood_{scan,sbom}.py` — consume the final image;
  neither cares about build-time guards.
- `pyproject.toml` runtime deps — unchanged.
- `memory/docker-standard.md` + `memory/health-checks.md` — the
  recipe doesn't mandate BuildKit; left as-is. Future BC can
  opinionate memory if operators ask.

---

## Non-goals

- **Fixing forge's publish atomicity.** Opening a separate FG-NNN
  ask so forge rebuilds both python bases atomically (block on both
  signing + push success before advancing `:latest`). BC-099 is
  beaconry-side defense-in-depth; the real root cause lives there.
- **Rebuilding + republishing beaconry's ghcr images from this
  branch.** Forge's CI owns the rebuild. Both forge bases are now
  consistent (both Python 3.14 as of 17:15:57 UTC) so the next
  scheduled `beaconry-hub` build produces a working image.
- **Supporting intentional minor-drift between builder + runtime.**
  Python C-extension wheels are ABI-bound to the exact minor; drift
  is a bug, not a feature.
- **Auto-retry.** If the guard trips, the build fails; the operator
  (or forge CI) investigates. Loud failure beats silent recovery
  for a supply-chain race.

---

## Design decisions

- **Named `runtime-src` stage instead of inline `--mount=from=<image>`.**
  BuildKit resolves `FROM <image>` to a digest at the start of the
  build; `--mount=from=<stage-name>` references the same resolved
  digest as the stage. If we inlined `--mount=from=ghcr.io/…:latest`,
  BuildKit might re-resolve the tag (rare but possible if caches
  expire mid-build) and the mount could pull a different digest
  from the runtime FROM. The named stage avoids this corner.
- **Probe via directory name, not binary execution.** Reading
  `/runtime-lib/python3.X/` for the directory name is cheap and
  doesn't require executing the runtime's `python3` binary inside
  the builder (whose shared-lib environment might differ). One
  `ls` + `grep` + `sed`. Deterministic.
- **Guard also asserts exactly one `python3.X` dir in the runtime
  base.** Hedges against a hypothetical forge bug that ships two
  co-existing python dirs in the same base image. Cheap check,
  loud failure.
- **Merge the guard into the existing BC-098 RUN step** rather than
  add a second RUN. Keeps the layer graph tight (one RUN = one
  layer). The mv step already needs `BUILDER_PY_VER`; the guard
  already produces `RUNTIME_PY_VER`; combining is the obvious
  read.
- **`# syntax=docker/dockerfile:1`** (floating minor) rather than
  pinning `1.7`. The `:1` tag is maintained by the Docker team and
  tracks the latest stable frontend. Pinning would require
  per-feature bumps.
- **Makefile `export` at the top** rather than per-target. Belt-and-braces:
  any make-invoked command inherits the env. Operators who invoke
  `docker build` outside make still get BuildKit via the Dockerfile
  syntax directive.

---

## Breaking changes

### 1. API / return shape

**None.** Endpoints, SBOM, callback_data unchanged.

### 2. Data model

**None.** No schema changes.

### 3. Behavioral semantics

- **Classic Docker builder rejected.** The `# syntax=…` directive
  requires BuildKit. Operators on Docker < 23 (which defaults to
  classic) must either `export DOCKER_BUILDKIT=1` or upgrade. Docker
  23 shipped October 2022 → no operator in beaconry's current
  reality is below it. Documented in the plan + the Dockerfile
  comment header.
- **Builds fail during forge publish races.** Pre-BC-099 the build
  would succeed and ship a broken image. Post-BC-099 it fails with
  a clear BC-099-marked error. This IS the intended behavioral
  change: loud ≫ silent. The remedy (wait + repull) is in the
  stderr message.
- **No change when bases are consistent.** When builder/runtime
  minors match (the >99% case), the guard runs + passes silently.
  The mv proceeds identically to BC-098.

---

## Merge plan

1. All work on `feat/BC-099-base-abi-guard`.
2. `make check` + `make check-full` pass locally. Both now exercise
   BuildKit via the syntax directive + Makefile env vars; the
   cold-start import probes remain the primary live oracle.
3. PR to `dev`. Claude opens, user runs `/ultrareview`, Claude runs
   `/code-review`, user merges when both clean.
4. Squash-merge.
5. Post-merge: INDEX flipped to `shipped`.
6. Downstream: forge's next `beaconry-{hub,scout}` rebuild picks
   up the guard automatically; from that moment on, any future
   forge publish race trips the build in forge CI, not in operators'
   deploys.

---

## Rollout plan

- **Dev loop**: `make live-rebuild` on this branch uses BuildKit
  (syntax directive) + probes both bases + builds normally since
  both forge bases are currently consistent (Python 3.14 on each).
- **Homelab (BC-094 pull path)**: next forge-side `beaconry-{hub,scout}`
  rebuild — triggered by forge's cron on next cycle — republishes
  working images. hetzner + server2 operators run `make deploy-beaconry`
  once that lands to recover from the already-broken image.
- **Operator action**: none for consumers; forge CI picks up the
  new Dockerfiles on their next rebuild.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| G1 — ABI guard present in both Dockerfiles | unit | `test_BC_099_base_abi_guard.py::test_abi_guard_run_step_present` | ✓ |
| G2a — Guard emits BC-099 marker | unit | `test_BC_099_base_abi_guard.py::test_abi_guard_emits_marker` | ✓ |
| G2b — Guard compares builder vs runtime minors | unit | `test_BC_099_base_abi_guard.py::test_abi_guard_compares_minors` | ✓ |
| G3a — `runtime-src` stage exists | unit | `test_BC_099_base_abi_guard.py::test_runtime_src_stage_present` | ✓ |
| G3b — Guard asserts exactly one python3.X dir in runtime base | unit | `test_BC_099_base_abi_guard.py::test_runtime_single_py_dir_assertion` | ✓ |
| G3c — Final image contains exactly one python3.X dir | integration (manual live) | `docker export $(docker create beaconry-hub:latest noop) \| tar -tf - \| grep -cE 'usr/local/lib/python3\.[0-9]+/$'` should return `1` | ✓ |
| G4a — `# syntax=docker/dockerfile:` directive at top of both | unit | `test_BC_099_base_abi_guard.py::test_syntax_directive_present` | ✓ |
| G4b — Makefile exports BuildKit env vars | unit | `test_BC_099_base_abi_guard.py::test_makefile_exports_buildkit` | ✓ |
| G5 — Cold-start import probes still pass | integration | `test_BC_083_forge_runtime.py::test_hub_can_import_all_runtime_deps` + scout equivalent (run under `make check-full`) | ✓ |
| G6 — CAP-T201 registered | lint | `make verify-traceability` | ✓ |
| Old BC-098 PY_VER step is subsumed, not duplicated | unit | `test_BC_099_base_abi_guard.py::test_bc_098_step_subsumed` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | TOML diff / N/A | N/A — nothing documented is deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

- Unit tests (grep-based) over both Dockerfiles + the Makefile.
  Fast, no Docker.
- `make check-full`'s cold-start stack exercises the guard
  end-to-end under BuildKit. If the guard mis-identifies the
  runtime minor, the cold-start import probes (which came up
  against a real container) catch it.
- **Manual live verification of G3c**: after `make live-rebuild`
  on this branch, `docker export … | tar -tf - | grep -cE
  'usr/local/lib/python3\.[0-9]+/$'` must return `1`. If `2+`,
  the ABI guard has a bug and let drift through. Documented here
  rather than automated in a test because it requires a live
  image; manual check is proportional.

---

## Testing commands

```bash
# Fast tier
make check

# Full tier — BuildKit path now; cold-start import probes are the oracle
BEACONRY_SKIP_DOGFOOD=1 make check-full

# Live check that only ONE python3.X dir exists in the final image
make live-rebuild
cid=$(docker create beaconry-hub:latest /app/hub.py)
docker export "$cid" | tar -tf - | grep -cE 'usr/local/lib/python3\.[0-9]+/$'
# Expected: 1
docker rm "$cid" >/dev/null

# Simulate a drift (optional — requires crafted test images):
# force builder to Python 3.X and runtime to Python 3.Y, then
# `docker build` should exit non-zero with the BC-099 marker in stderr.
```

---

## Guard failure message (final wording)

```
BC-099 ABI guard tripped: forge base Python minors disagree.
  forge-builder-trixie:latest  → Python ${BUILDER_PY_VER}
  python-scratch-trixie:latest → Python ${RUNTIME_PY_VER}

Cause: forge republished one base's :latest tag before the other
during a Python minor bump. Proceeding would ship wheels at
/usr/local/lib/python${BUILDER_PY_VER}/site-packages/, which the
runtime's Python ${RUNTIME_PY_VER} interpreter would not find on
sys.path.

Remedy: wait for forge's next cron rebuild to republish the lagging
base, then:
  docker pull ghcr.io/bboortz/forge-builder-trixie:latest
  docker pull ghcr.io/bboortz/python-scratch-trixie:latest
  <re-run the build>

See docs/stories/BC-099-base-abi-guard.md.
```

---

## Links

- Story: `docs/stories/BC-099-base-abi-guard.md`
- BC-098 (predecessor — pattern this story defends)
- BC-083 (cold-start import probes — oracle)
- Forge FG-032 (origin of the publish race)
- Forge PR #60: `https://github.com/bboortz/forge/pull/60`
- Incident: 2026-11-08 ~17:26 UTC, hetzner + server2,
  `ModuleNotFoundError: No module named 'uvicorn'`.
