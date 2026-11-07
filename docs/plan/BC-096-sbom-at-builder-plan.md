# BC-096 — SBOM at builder stage → baked into runtime + `/sbom` + `make dogfood-sbom`

*Branch: `feat/BC-096-sbom-at-builder`*
*Start: 2026-11-07*
*Story: `docs/stories/BC-096-sbom-at-builder.md`*

---

## Situation

`dogfood_scan.py` (BC-082) runs `grype <beaconry-hub:latest>` via the
Docker socket. Every grype DB refresh can flip the verdict without
anything inside beaconry changing. BC-095's merge demonstrated this
live: syft v1.57.0's brotli dep tripped GO-2026-6888 at dogfood time
despite no beaconry regression — the escape hatch was
`BEACONRY_SKIP_DOGFOOD=1`.

`ghcr.io/bboortz/forge-builder-trixie:latest` (now beaconry's builder
base, BC-095) already ships `syft` on PATH. That's the one dependency
that made FG-020's SBOM-at-builder pattern cheap on the forge side —
and it's cheap on the beaconry side now too.

---

## Problems to solve

- Dogfood signals are conflated: "image regressed" vs "grype DB
  learned a new CVE". Operators (and reviewers) can't tell which
  from a failed dogfood-scan.
- No operator-visible SBOM for the hub/scout images themselves.
  (Scout generates SBOMs for *other* images; nothing documents what's
  inside hub/scout.)
- `dogfood_scan.py`'s current invocation needs `/var/run/docker.sock`
  mounted. An SBOM-based scan doesn't — reduces the privileges needed
  for self-scan.

---

## Goals

1. **G1 — Hub builder generates `/sbom.json`.** Builder RUN step calls
   syft against `/install` and writes `syft-json` to `/sbom.json`.
2. **G2 — Hub runtime bakes in the SBOM.** `COPY --from=builder
   /sbom.json /sbom.json` in the runtime stage.
3. **G3 — Scout mirrors G1 + G2.**
4. **G4 — `/sbom` endpoint on both services** returns the baked-in
   JSON as `application/json`.
5. **G5 — `make dogfood-sbom` + `poc/scripts/dogfood_sbom.py`** run
   `grype sbom:/sbom.json` inside each image, fail on High+ fixable
   with the same shape as `dogfood_scan.py`.
6. **G6 — `make check-full` runs both dogfood passes.** The two
   signals are both gated; neither replaces the other.
7. **G7 — CAP-T197 registered**, bidirectional link to tests.

---

## Scope

### Modified files

- `poc/hub/Dockerfile`:
  - After the `COPY hub/*.py …` block (so the SBOM reflects source +
    deps), add a `RUN syft dir:/install -o syft-json=/sbom.json`.
  - In the runtime stage, add `COPY --from=builder /sbom.json /sbom.json`.
- `poc/scout/Dockerfile`:
  - After the `COPY scout/scout.py` + `beaconry_health/` block, add the
    same syft RUN.
  - Same runtime COPY.
- `poc/hub/hub.py`: new `@api.get("/sbom")` returning `FileResponse`
  for `/sbom.json` with `media_type="application/json"`. Degrades to
  503 if the file is missing (dev/host runs without a baked-in SBOM).
- `poc/scout/scout.py`: new `@app.get("/sbom")` with the same shape.
- `Makefile`:
  - Add `dogfood-sbom` target.
  - Add it to `check-full`'s run list (after `dogfood-scan`).
  - Update the help block.
- `docs/VARIABLES.md`: no new vars (BC-096 is additive, no knobs).
- `docs/CAPABILITIES.md`: insert CAP-T197 row.
- `docs/stories/INDEX.md`: BC-096 in-progress row.
- `docs/plan/IDEAS.md`: strike the BC-096 candidate entry with
  "Shipped in BC-096 (2026-11-07)".
- `CHANGELOG.md`: v0.16.4 entry.
- `poc/VERSION`: `0.16.3` → `0.16.4`.

### Added files

- `poc/scripts/dogfood_sbom.py` — new entrypoint; mirrors
  `dogfood_scan.py` but runs `grype sbom:/sbom.json` inside each image.
  No docker socket mount required.
- `poc/tests/unit/test_BC_096_sbom_at_builder.py` — Dockerfile greps
  + endpoint smoke tests via FastAPI TestClient.
- `poc/tests/unit/test_BC_096_dogfood_sbom.py` — unit test for the new
  script's `parse_findings` pure-function (same shape as
  `dogfood_scan.py` → reuse the shape).

### Not modified

- `poc/scripts/dogfood_scan.py` — image-scan path stays untouched.
- `docker-compose.yml` or overrides — no env/port changes.
- `poc/beaconry_health/` — `/sbom` is a non-health endpoint (it answers
  "what's in me?", not "am I healthy?"). It lives inline in each
  service file, next to the other service-specific routes
  (`/scan/trigger`, `/scan`, `/findings`, …).

---

## Non-goals

- Replacing `dogfood_scan.py`. The image-scan catches Go-stdlib bugs in
  built binaries that `dir:/install` syft cannot see; it stays.
- Multi-format SBOM (CycloneDX / SPDX conversion). `syft-json` is
  grype-native and format-stable — no extra conversion tax.
- Cosign attestations. The SBOM is part of the image layer under the
  existing BC-094 image signature chain.
- Scanning the final runtime image layout (binaries in `/usr/local/bin`,
  `/app`). Explicit non-goal — would duplicate image-scan.

---

## Design decisions

- **`syft dir:/install` only** (python wheel site-packages). Covers
  the primary vector. Binaries (syft/grype/docker Go stdlib) are
  explicitly out-of-scope — the image-scan path covers them. Rationale:
  the simplest invocation that delivers the "frozen snapshot"
  property. A staging-rootfs approach that mirrors runtime layout +
  binaries is strictly larger-scope and not needed for G1–G7.
- **`syft-json` output format**. Grype's native format; no translation
  layer. If a future BC needs CycloneDX, it's a one-flag change on the
  syft invocation.
- **`/sbom.json` at image root** (not under `/app/` or `/data/`).
  Simple path, no permission concerns at runtime (the file is
  world-readable from the COPY), no interaction with the chown-ed
  `/data` volume. FG-020 uses the same convention.
- **Hub + scout serve `/sbom` with the same shape**. Operator mental
  model: "any beaconry-shipped service exposes `/sbom`". Future
  services inherit automatically if they adopt the pattern.
- **New `dogfood_sbom.py` file rather than a mode flag on
  `dogfood_scan.py`**. Keeps `dogfood_scan.py`'s current interface
  and lint surface untouched. The two scripts share the `parse_findings`
  shape; `dogfood_sbom.py` imports it.
- **Both dogfood paths run in `check-full`, not `check`**. `make check`
  is the fast tier; neither path belongs there (both require the
  images built). `check-full` already pulls the images for e2e +
  scenarios; adding dogfood-sbom costs one extra `docker run` per
  image.
- **No new env var** (per the "no new knobs" bias). Operators who
  want to disable dogfood-sbom can comment the Makefile line or use
  the same `BEACONRY_SKIP_DOGFOOD=1` escape hatch — the new script
  honours it for the same emergency-escape reason.

---

## Breaking changes

### 1. API / return shape

- **New `GET /sbom` route on hub + scout.** Additive. Pre-BC-096
  clients never hit it; post-BC-096 clients see `200 application/json`
  with the syft-json body. No existing route changes shape.
- **No change to any audit event name, callback_data, or scout POST
  payload.** Existing callers unaffected.

### 2. Data model (schema + legacy rows)

- **No schema changes.** No new tables, columns, or audit event types.
- **No migration needed.** BC-096 is pure build + serve additions.

### 3. Behavioral semantics

- **`make check-full` runs one extra step.** Pre-BC-096 operators who
  rely on `make check-full` passing see the same pass/fail outcome
  **unless** dogfood-sbom catches a High+ fixable that dogfood-scan
  missed. Rationale: that's the whole point — SBOM-scan surfaces
  python-dep CVEs that live-image-scan could miss if the binary
  cataloguer skips a package. Opt-out: `BEACONRY_SKIP_DOGFOOD=1`
  skips **both** passes (same escape hatch as BC-082).
- **Image size.** The baked-in `/sbom.json` adds roughly 50–200 KB to
  each runtime image. Negligible for a ~100 MB base; called out so
  nobody is surprised during image-size regression checks.

---

## Merge plan

1. Work on `feat/BC-096-sbom-at-builder`.
2. `make check` passes on branch tip. `make check-full` passes
   (includes the new dogfood-sbom gate).
3. PR to `dev`. Claude opens, user runs `/ultrareview`, Claude runs
   `/code-review`, user merges (or self-merge per standing
   authorization if green).
4. Squash-merge.
5. Post-merge: INDEX.md updated to `shipped`.

---

## Rollout plan

- **Dev loop**: `make live-rebuild` on next pull picks up the new
  Dockerfiles. `docker exec beaconry-hub ls -l /sbom.json` confirms
  the SBOM landed.
- **Homelab (BC-094 pull path)**: forge rebuilds beaconry images on
  the next CI run; the next `make deploy-beaconry` pulls the new
  images with the baked-in SBOM. No homelab compose/role edits
  required.
- **Operator action**: none. Curling `/sbom` is opt-in; `make
  dogfood-sbom` is opt-in locally but part of `make check-full` in CI.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| G1 — Hub builder generates `/sbom.json` | unit | `test_BC_096_sbom_at_builder.py::test_hub_builder_runs_syft` | ✓ |
| G2 — Hub runtime bakes in the SBOM | unit | `test_BC_096_sbom_at_builder.py::test_hub_runtime_copies_sbom` | ✓ |
| G3a — Scout builder generates `/sbom.json` | unit | `test_BC_096_sbom_at_builder.py::test_scout_builder_runs_syft` | ✓ |
| G3b — Scout runtime bakes in the SBOM | unit | `test_BC_096_sbom_at_builder.py::test_scout_runtime_copies_sbom` | ✓ |
| G4a — Hub `/sbom` route exists + returns JSON | unit | `test_BC_096_sbom_at_builder.py::test_hub_sbom_endpoint_returns_file` | ✓ |
| G4b — Scout `/sbom` route exists + returns JSON | unit | `test_BC_096_sbom_at_builder.py::test_scout_sbom_endpoint_returns_file` | ✓ |
| G4c — Both `/sbom` endpoints 503 if the file is missing | unit | `test_BC_096_sbom_at_builder.py::test_sbom_endpoint_503_without_file` | ✓ |
| G5a — dogfood-sbom runs grype with `sbom:/sbom.json` | unit | `test_BC_096_dogfood_sbom.py::test_scan_image_cmd_uses_sbom_source` | ✓ |
| G5b — dogfood-sbom parse_findings filters High+ fixable | unit | `test_BC_096_dogfood_sbom.py::test_parse_findings_filters_high_fixable` | ✓ |
| G5c — dogfood-sbom honours `BEACONRY_SKIP_DOGFOOD` | unit | `test_BC_096_dogfood_sbom.py::test_main_respects_skip_env` | ✓ |
| G6 — `make check-full` runs dogfood-sbom | manual + Makefile diff | `grep "dogfood-sbom" Makefile` → in `check-full` target | ✓ |
| G7 — CAP-T197 registered + bidirectional | lint | `make verify-traceability` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | TOML diff / N/A | N/A — nothing deprecated; additive | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

- Unit lint tests grep both Dockerfiles for the syft RUN + COPY lines.
- Unit tests spin up each FastAPI app via `TestClient`, point the
  `/sbom` file-path at a tempfile fixture, GET `/sbom`, parse JSON,
  and (second test) remove the tempfile to assert 503.
- Unit tests for `dogfood_sbom.py`:
  - `parse_findings` filter semantics (reuse the dogfood_scan shape
    verbatim; one shared-shape fixture file in `tests/fixtures/`).
  - `_scan_image` cmd assembly — assert `sbom:/sbom.json` is present
    in the argv list and no `-v /var/run/docker.sock` mount.
  - `main` respects `BEACONRY_SKIP_DOGFOOD=1`.
- Full tier (`make check-full`) actually runs dogfood-sbom against
  the built images and surfaces real findings. This is the integration
  cover — unit tests do not spin docker.

---

## Testing commands

```bash
# Fast tier (no images required)
make check

# Full tier — builds images, runs scenarios + e2e + cold-start + both dogfood paths
make check-full

# Just the new path (requires images to be built — `make live-rebuild` first)
make dogfood-sbom

# Spot-check /sbom on a running stack
curl -s http://localhost:8000/sbom | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('artifacts',[])))"
curl -s http://localhost:8001/sbom | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('artifacts',[])))"
```

Expected: integer > 0 for each — the count of python packages the SBOM
observed during build.

---

## Links

- Story: `docs/stories/BC-096-sbom-at-builder.md`
- BC-095 (sibling — unblocks `syft` already on builder PATH)
- BC-082 (sibling — image-scan path that this complements)
- CAP-T197 (this BC's new capability)
- Forge FG-020 (pattern origin)
