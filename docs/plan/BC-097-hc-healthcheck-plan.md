# BC-097 — Compose healthchecks use forge's `/hc`; runtime base tracks `:latest`

*Branch: `feat/BC-097-hc-healthcheck`*
*Start: 2026-11-08*
*Story: `docs/stories/BC-097-hc-healthcheck.md`*

---

## Situation

Current compose healthcheck convention (BC-010, canonicalized in
`memory/docker-standard.md § Healthcheck patterns`):

```yaml
test: ["CMD", "python3", "-c",
       "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/readyz').status==200 else 1)"]
```

Correct — scratch runtime has no shell/curl so the inline python3
interpreter was the right workaround. Steady-state cost: a full
CPython cold-start per probe every 30 s per container.

Forge FG-031 (shipped 2026-11-08, image digest
`sha256:693f61d7b744…`) adds `/hc` to every `*-scratch-trixie`
image — a ~400 KB static binary with the surface:

```
hc [-t secs] [-s lo-hi] [-m str] [-H name] [-k | --ca file]
   (tcp|tls|http|https)://host[:port][/path]
hc idle                                      # container PID 1 block
```

Exit 0 iff the probe matches; default timeout + default expected
status (2xx). For beaconry's readyz probes we only need `-t 3
http://localhost:<port>/<path>`.

Beaconry's runtime stage is still pinned to
`ghcr.io/bboortz/python-scratch-trixie:3.13` (BC-083). The `:3.13`
semver-pinned tag lags `:latest`'s rebuild cadence — every FG-030/031
patch on the forge side would need to wait for the next `:3.13`
rebuild window to reach us. `:latest` matches how we already consume
`forge-builder-trixie:latest` on the builder side (BC-095).

---

## Problems to solve

- Shrink per-probe cost (static binary beats Python cold-start).
- Standardize on forge's `/hc` vocabulary — shared with every other
  scratch-base consumer.
- Close the asymmetry: builder is `:latest`, runtime is `:3.13`. One
  cadence for both.
- Keep the python-inline recipe documented as the fallback for
  services that don't inherit from a forge base.

---

## Goals

1. **G1** — Both `poc/{hub,scout}/Dockerfile` runtime stage `FROM
   ghcr.io/bboortz/python-scratch-trixie:latest AS runtime`. No
   `:3.13` reference remains.
2. **G2** — `verify_pinned_versions.py`'s `RUNTIME_IMAGE_RE` accepts
   `:latest` alongside the existing `:3.13` + distroless fallbacks.
3. **G3** — Every compose healthcheck in the 5 compose files uses
   `["CMD", "/hc", "-t", "3", "http://localhost:<port>/<path>z"]`.
   Six probe sites total. Zero inline `python3 -c` probes remain.
4. **G4** — `memory/docker-standard.md § Healthcheck patterns` +
   `memory/health-checks.md § Compose consumes only one healthcheck`
   lead with `/hc`; python-inline retained as a fallback paragraph
   for non-forge-based services.
5. **G5** — `make check-full` green end-to-end (cold-start +
   scenarios + e2e all exercise `/hc` live).
6. **G6** — CAP-T198 + CAP-T199 registered, bidirectional-linked.

---

## Scope

### Modified files

- `poc/hub/Dockerfile` — runtime FROM `:3.13` → `:latest`; refresh
  the preceding comment block to name BC-097 + call out the risk
  (Python-minor-version drift under `:latest` → cold-start smoke
  catches it).
- `poc/scout/Dockerfile` — same runtime FROM update.
- `poc/scripts/verify_pinned_versions.py` — extend
  `RUNTIME_IMAGE_RE` to include the `:latest` tag. Update the
  module docstring + error messages to name BC-097.
- `poc/docker-compose.yml` — hub + scout healthcheck blocks.
- `poc/docker-compose.cold-start.yml` — hub + scout healthcheck
  blocks.
- `poc/docker-compose.scout-only.yml` — scout healthcheck block.
- `poc/scenarios/compose.yml` — hub healthcheck block (BC-038:
  points at `/startupz`, same `/hc` swap applies — just different
  path).
- `memory/docker-standard.md` — rewrite the healthcheck section to
  lead with `/hc -t 3 …`; keep the python-inline form as a labeled
  fallback for images without `/hc`.
- `memory/health-checks.md` — same rewrite in the compose example.
- `docs/CAPABILITIES.md` — CAP-T198 + CAP-T199 entries, both
  bidirectionally linked to the new test file.
- `docs/stories/INDEX.md` — BC-097 row.
- `CHANGELOG.md` — v0.16.5 entry.
- `VERSION` + `poc/VERSION` + `pyproject.toml` — 0.16.4 → 0.16.5.

### Added files

- `poc/tests/unit/test_BC_097_hc_healthcheck.py` — grep every compose
  file for the `/hc` healthcheck shape, assert no `python3 -c`
  remains; grep both Dockerfiles for the `:latest` runtime FROM;
  assert `verify_pinned_versions.py` passes on branch tip.

### Not modified

- `poc/beaconry_health/` — endpoint code is unchanged (`/hc` probes
  the same endpoints beaconry already serves).
- Python app code (hub.py, scout.py, …) — no runtime behavior change.
- `.beaconry-cache/` — nothing cached that `/hc` affects.

---

## Non-goals

- Replacing the python-inline recipe entirely — it stays documented
  as the fallback for images that don't inherit `/hc`.
- Baking a `HEALTHCHECK` instruction into the Dockerfiles. FG-031
  intentionally declined this for python-scratch (consumer ports/paths
  vary). Compose remains the right layer.
- Pinning the `/hc` binary version. We consume whatever `/hc` the
  forge base ships; if forge ships a breaking `/hc` upgrade, cold-start
  smoke catches it.
- Changing the probe target endpoints (`/readyz` for live stacks,
  `/startupz` for scenarios per BC-038). Only the probe mechanism
  changes.
- Pinning the forge runtime to a specific digest for reproducibility.
  Operators who want that can do it downstream; beaconry stays on
  `:latest` symmetrically with the builder side.

---

## Design decisions

- **Timeout `-t 3`**: BC-082's existing cold-start wait of 180 s
  dwarfs any single-probe timeout; 3 s is short enough to catch a
  wedged service quickly, long enough to tolerate a slow readyz
  under KEV refresh load.
- **Explicit `http://localhost:<port>`**: `/hc`'s grammar requires a
  scheme + authority. We keep the explicit form rather than any
  shorthand so the probe target is unambiguous in compose YAML.
- **`:latest` for runtime** (not `:3.13`): symmetry with
  `forge-builder-trixie:latest`. The `:3.13` semver-pinned tag
  exists for downstream consumers who need Python-minor stability —
  beaconry already tolerates minor bumps because site-packages are
  built from the same forge toolchain each time.
- **Keep `:3.13` + distroless fallbacks in `RUNTIME_IMAGE_RE`**: no
  reason to be aggressive about removing them yet. An operator
  rolling back to a prior build (BC-095-style) shouldn't trip the
  pinned-versions lint.
- **Memory docs lead with `/hc`, retain python-inline as fallback**:
  the fallback is still correct and necessary for services on bases
  without `/hc` (e.g. the hypothetical "very narrow sidecar" case
  BC-013 preserved for file-sentinels).

---

## Breaking changes

### 1. API / return shape

**None.** No HTTP contract, callback_data, or audit event names
change. `/hc` is just a probe-side mechanism; endpoints are unchanged.

### 2. Data model (schema + legacy rows)

**None.** No schema changes.

### 3. Behavioral semantics

- **Probe mechanism**: `python3 -c urllib.request...` → `/hc -t 3 …`.
  Both exit 0 iff the HTTP response was 2xx. Equivalent surface; the
  only observable change is faster probe startup (~400 KB static
  binary vs. CPython cold start).
- **Runtime base**: `python-scratch-trixie:3.13` → `:latest`. Both
  tags point at Python 3.13 today. A future forge bump to Python
  3.14 would break `COPY /install/lib/python3.13/...` paths — the
  cold-start smoke test catches this on the first rebuild. Opt-out:
  pin back to `:3.13` locally if an operator's build cadence can't
  tolerate the risk.

---

## Merge plan

1. All work on `feat/BC-097-hc-healthcheck`.
2. `make check` passes on branch tip; `make check-full` passes
   end-to-end (cold-start + scenarios + e2e exercise `/hc` live
   against the new forge image).
3. PR to `dev`. Claude opens, user runs `/ultrareview`, Claude runs
   `/code-review`, user merges on green (standing authorization
   permits self-merge if both reviews are clean).
4. Squash-merge.
5. Post-merge: INDEX.md flipped to `shipped`.

---

## Rollout plan

- **Dev loop**: next `make live-rebuild` on this branch pulls the
  `/hc`-bearing forge base; compose probes exercise `/hc` within 60 s
  of start.
- **Homelab (BC-094 pull path)**: forge CI rebuilds
  `ghcr.io/bboortz/beaconry-{hub,scout}` from this source on the next
  trigger. Next `make deploy-beaconry` pulls the rebuilt images. No
  homelab compose/role change required — the compose healthcheck
  edits live inside the images via `poc/docker-compose.yml`.
- **Operator action**: none. Compose probe syntax changes are
  invisible — just `docker inspect ... --format {{.State.Health.Status}}`
  reaches `healthy` as before.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| G1 — Both Dockerfile runtime FROM = `:latest` | unit | `test_BC_097_hc_healthcheck.py::test_runtime_from_tracks_latest` | ✓ |
| G1b — No `:3.13` runtime reference remains | unit | `test_BC_097_hc_healthcheck.py::test_no_colon_3_13_runtime_remains` | ✓ |
| G2 — `verify_pinned_versions.py` accepts `:latest` | unit + lint | `test_BC_097_hc_healthcheck.py::test_verify_pinned_versions_passes_on_branch` + `make verify-pinned-versions` | ✓ |
| G3 — Every compose healthcheck uses `/hc -t 3` | unit | `test_BC_097_hc_healthcheck.py::test_all_healthchecks_use_hc` | ✓ |
| G3b — Zero inline `python3 -c` healthcheck probes | unit | `test_BC_097_hc_healthcheck.py::test_no_inline_python_healthchecks` | ✓ |
| G4 — Memory docs lead with `/hc` | grep | `grep -n "/hc" memory/docker-standard.md memory/health-checks.md` + manual | ✓ |
| G5 — cold-start + scenarios + e2e all green | gate log | `make check-full` on pre-push | ✓ |
| G6 — CAP-T198 + CAP-T199 registered | lint | `make verify-traceability` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | TOML diff / N/A | N/A — python-inline retained as documented fallback | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

- Unit tests (grep-based) over Dockerfiles + compose YAML + lint
  module. No live container needed to assert text-shape correctness.
- Cold-start smoke (BC-082, part of `make check-full`) is the live
  gate: brings up the entire compose stack with fresh volumes +
  real forge images, asserts all health endpoints reach ready
  within 180 s. If `/hc` fails (wrong argv shape, missing binary,
  etc.), cold-start times out.
- Scenarios + e2e likewise exercise the healthchecks against live
  containers.

---

## Testing commands

```bash
# Fast tier
make check

# Full tier — rebuilds images with the new forge base, runs cold-start + scenarios + e2e
make check-full

# Spot-check probe shape against a running stack
docker inspect beaconry-hub --format '{{.Config.Healthcheck.Test}}'
# Expected: [CMD /hc -t 3 http://localhost:8000/readyz]
docker inspect beaconry-hub --format '{{.State.Health.Status}}'
# Expected: healthy
```

---

## Links

- Story: `docs/stories/BC-097-hc-healthcheck.md`
- Forge FG-031 (origin of `/hc` in python-scratch-trixie):
  `~/git/forge/docs/stories/FG-031-hc-in-service-images.md`
- Forge FG-030 (`/hc` tool itself):
  `~/git/forge/docs/stories/FG-030-healthy-scratch-v2.md`
- BC-010 (four-endpoint convention — same endpoints, new probe tool)
- BC-013 (HTTP probes beat file-sentinels)
- BC-083 (runtime migration to forge scratch)
- BC-095 (builder migration to `forge-builder-trixie:latest` — sibling
  pattern, also `:latest`)
- CAP-T171 (`make verify-base-images`) — picks up the new ref automatically
