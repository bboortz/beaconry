---
id: BC-096
title: SBOM at builder stage → baked into runtime + `/sbom` endpoint + `make dogfood-sbom`
status: in-progress
created: 2026-11-07
branch: feat/BC-096-sbom-at-builder
plan: docs/plan/BC-096-sbom-at-builder-plan.md
---

# BC-096: SBOM at builder stage — adopt forge's FG-020 pattern for beaconry's own images

## Narrative

Beaconry's `dogfood_scan.py` (BC-082) scans the built `beaconry-hub:latest`
and `beaconry-scout:latest` images with grype against the live image.
That path answers "is there a High+ fixable CVE right now?" — but it
conflates two very different reasons an answer can change between
runs:

1. **Image regressed** — a dep bump or base-image rebuild introduced
   a new vulnerable package → the image's bill of materials actually
   changed.
2. **grype DB learned a new CVE** — the image is byte-equivalent to
   yesterday's, but grype's vulnerability feed grew a new entry that
   now matches an existing package → nothing in beaconry changed.

Both signals are valuable; both need different operator responses.
Today they're indistinguishable from a `make dogfood-scan` failure.
BC-095's merge revealed the asymmetry acutely: a syft v1.57.0 brotli
CVE (GO-2026-6888) was reported by dogfood-scan against an
otherwise-clean beaconry build. The two-signal confusion made the
PR reviewer path "opt out with `BEACONRY_SKIP_DOGFOOD=1` and hope the
next grype feed update + syft release resolves it" instead of
"confirm the image didn't change, wait out the upstream fix".

Forge's **FG-020** (Dockerfile SBOM pattern) solves this: syft the
builder stage, COPY the resulting SBOM into the final runtime image,
then grype the baked-in SBOM. The SBOM is a **frozen snapshot** of
what's in the image — it doesn't change unless the image is rebuilt.
Grype DB updates re-scanning the same frozen SBOM = signal (2).
Image rebuild + rescan the fresh SBOM = signal (1).

BC-096 adopts the same pattern for `beaconry-hub` + `beaconry-scout`:

- **Builder stage** generates `syft dir:/install -o syft-json=/sbom.json`
  (captures python wheel deps — the primary vector).
- **Runtime stage** does `COPY --from=builder /sbom.json /sbom.json`
  so the SBOM travels with the image.
- **New `/sbom` endpoint** on both services serves the baked-in SBOM
  as JSON — operators can curl it for audit tooling, future compliance,
  or ad-hoc grype scans.
- **New `make dogfood-sbom` target + `poc/scripts/dogfood_sbom.py`** runs
  `grype sbom:/sbom.json` *inside* each image (no `docker.sock` mount
  needed). Fails on High+ fixable just like `dogfood_scan.py` does.

Dogfood-scan (image-scan) and dogfood-sbom (SBOM-scan) are
**complementary**, not competing. Image-scan is the catch-all
(including Go-stdlib bugs embedded in syft/grype/docker binaries, which
`dir:/install` doesn't see). SBOM-scan is the deterministic
python-dep lens. BC-096 runs both in `check-full`.

## Trigger

Not event-driven. Follow-on to BC-095 (builder migration to forge).
Resolves the IDEAS entry "SBOM captured at builder stage → consumed
by dogfood_scan + exposed on /sbom" (open 2026-11-07, scheduled as
BC-096 in `docs/plan/IDEAS.md`).

## Expected outcome

- Both Dockerfiles run `syft dir:/install -o syft-json=/sbom.json` in
  the builder stage; both runtime stages `COPY --from=builder /sbom.json`.
- `docker run --rm --entrypoint /usr/local/bin/python3 beaconry-hub:latest -c "import os; print(os.stat('/sbom.json').st_size)"`
  prints a non-zero byte count.
- `curl http://localhost:8000/sbom` on a running hub returns the baked-in
  SBOM as `application/json`. Same for `curl http://localhost:8001/sbom`
  on scout.
- `make dogfood-sbom` runs grype against each image's embedded SBOM
  via `docker run --entrypoint /usr/local/bin/grype <image> sbom:/sbom.json`.
  Fails on High+ fixable.
- `make check-full` runs both `dogfood-scan` AND `dogfood-sbom`.
- `docs/CAPABILITIES.md` registers **CAP-T197**.

## Out of scope

- **Scanning builder binaries** (syft/grype/docker Go stdlib) into the
  SBOM. Covered by the complementary image-scan path. A future BC could
  extend syft coverage via a staging-rootfs approach if the overlap
  becomes a problem; not needed today.
- **Pinning the SBOM to a specific CycloneDX/SPDX version string**.
  `syft-json` is grype-native and version-stable; CycloneDX/SPDX
  conversion is a future compliance-driven BC.
- **Signing or attaching the SBOM via cosign attestations**. The SBOM
  is baked into the image layer and inherits the image's own
  signature trust chain (BC-094). Attestations are a separate mechanism
  if operators ever need them.
- **Hub endpoint auth on `/sbom`**. The SBOM contains package names +
  versions already visible from `syft <image>` externally; no secret
  material. Follows the same unauth posture as `/healthz`.

## Acceptance criteria (Gherkin)

```gherkin
Feature: beaconry images carry a baked-in SBOM

  Scenario: Hub builder generates an SBOM
    Given a checkout of beaconry on this branch
    When a reader greps poc/hub/Dockerfile for `syft dir:/install`
    Then exactly one builder RUN matches and writes to `/sbom.json`

  Scenario: Hub runtime copies the SBOM in
    When a reader greps poc/hub/Dockerfile for `COPY --from=builder /sbom.json`
    Then exactly one COPY line matches

  Scenario: Scout builder generates an SBOM
    Same as hub but for poc/scout/Dockerfile.

  Scenario: Scout runtime copies the SBOM in
    Same as hub but for poc/scout/Dockerfile.

  Scenario: Hub /sbom endpoint serves the baked-in SBOM
    Given hub is running
    When a client GETs http://localhost:8000/sbom
    Then the response is 200 application/json
     And the body parses as valid JSON
     And the body contains a top-level `artifacts` key (syft-json shape)

  Scenario: Scout /sbom endpoint serves the baked-in SBOM
    Same as hub but port 8001.

  Scenario: `make dogfood-sbom` scans each image's embedded SBOM
    Given both beaconry images are built
    When the operator runs `make dogfood-sbom`
    Then grype runs against each image's `/sbom.json`
     And the exit code is 0 iff no High/Critical fixable CVE is matched

  Scenario: `make check-full` runs both dogfood passes
    When the operator runs `make check-full`
    Then dogfood-scan AND dogfood-sbom both execute
     And the gate fails iff either fails
```

## Capabilities delivered

- **CAP-T197: Baked-in SBOM at builder stage + dogfood-sbom
  grype-against-frozen-SBOM path** (type: technical, status: active).
  Adopts forge's FG-020 pattern. Separates "image regressed" from
  "grype DB learned a new CVE" by giving operators a deterministic
  frozen-SBOM scan alongside the live image-scan.

Reference existing CAPs:
- Extends CAP-T167 (`make dogfood-scan` image-scan gate) with a
  complementary frozen-SBOM path.
- Supports CAP-B023 (operator trust in beaconry's own supply chain).
- Depends on CAP-T196 (BC-095 — forge builder ships syft).

## Links

- Forge pattern: FG-020 "Dockerfile SBOM — syft at build, grype at scan"
- BC-082 (dogfood-scan, image-scan path).
- BC-095 (builder migration to forge-builder-trixie — unblocks
  "syft already on PATH in builder").
- CAP-T167 (image-scan gate), CAP-T171 (verify-base-images).
- IDEAS entry: `docs/plan/IDEAS.md § "SBOM captured at builder stage"`.
