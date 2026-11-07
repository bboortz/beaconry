# BC-095 — Builder stage FROM `ghcr.io/bboortz/forge-builder-trixie:latest`

*Branch: `feat/BC-095-builder-forge-trixie`*
*Start: 2026-11-07*
*Story: `docs/stories/BC-095-builder-forge-trixie.md`*

---

## Situation

Beaconry's two Dockerfiles use a hybrid supply chain today:

- **Runtime stage**: `ghcr.io/bboortz/python-scratch-trixie:3.13` —
  forge-managed, daily-rebuilt, cosign-signed (BC-083).
- **Builder stage**: `python:3.13-slim-trixie` — upstream Docker Hub
  Library. No signature, no beaconry-controlled patch cadence.

Forge publishes `ghcr.io/bboortz/forge-builder-trixie:latest` (first
published 2026-11-07 17:14 UTC per OCI labels). Verified contents:

```
$ docker run --rm --entrypoint sh ghcr.io/bboortz/forge-builder-trixie:latest \
    -c "which syft python3 pip; python3 --version"
/usr/local/bin/syft
/usr/local/bin/python3
/usr/local/bin/pip
Python 3.13.18
```

Also inside: `ca-certificates`, `tar` (apt packages, baked in). NOT
inside: `curl`, `grype`, `docker` CLI.

## Problem

Swap the two builders' FROM line to the forge-signed base. Delete
install steps whose outputs are already baked in. Keep install
steps whose outputs aren't (grype + docker CLI on hub side).

## Goals

1. Hub builder FROM uses forge-builder-trixie.
2. Scout builder FROM uses forge-builder-trixie.
3. No upstream `python:…` FROM remains in either Dockerfile.
4. Builds + smokes succeed on `make check-full` (scenarios + e2e +
   cold-start).
5. `make verify-base-images` picks up the new ref (automatic — it
   scans every FROM).

## Scope

### Modified files
- `poc/hub/Dockerfile`:
  - Builder FROM → `ghcr.io/bboortz/forge-builder-trixie:latest`.
  - Delete the syft install RUN.
  - Consolidate the curl apt-install → grype fetch → docker CLI
    fetch → curl purge into fewer RUN layers (curl is a build-time
    tool here; purge it after both static fetches).
  - Comments reference BC-095 next to the new FROM and the
    "curl purged after static fetches" cleanup.
- `poc/scout/Dockerfile`:
  - Builder FROM → `ghcr.io/bboortz/forge-builder-trixie:latest`.
  - Delete the curl apt-install RUN **and** the syft install RUN.
    Scout's builder prelude becomes zero RUN lines before the
    `WORKDIR /app` — forge-builder's base fully covers scout.

### Added files
- `poc/tests/unit/test_BC_095_builder_from.py` — greps both
  Dockerfiles for the expected FROM ref; asserts no legacy
  `python:…` FROM remains; asserts hub still installs grype + docker
  CLI; asserts the builder's single remaining purpose (final COPY
  layout) survives.

### Added docs
- `docs/stories/BC-095-builder-forge-trixie.md` (story).
- `docs/plan/BC-095-builder-forge-trixie-plan.md` (this file).
- `docs/CAPABILITIES.md` row for CAP-T196.
- `docs/stories/INDEX.md` row for BC-095.
- `CHANGELOG.md` v0.16.3 entry.
- `docs/plan/IDEAS.md` entry for BC-096 (SBOM-at-builder follow-up).

### Not modified
- Runtime stage of both Dockerfiles — still
  `ghcr.io/bboortz/python-scratch-trixie:3.13`. BC-083 stands.
- Any Python / shell code.
- `poc/docker-compose.yml` + overrides — image ref semantics
  unchanged.
- `dogfood_scan.py`, `cleanup.py` — they consume the final runtime
  image which is byte-equivalent.

## Design decisions

- **One forge base vs two (builder + runtime same digest)**: no.
  Forge's `python-scratch-trixie` is scratch-runtime; `forge-builder-
  trixie` is build-capable. They're intentionally different bases —
  sharing them would defeat the "no shell, no apt" runtime guarantee.
- **Keep grype + docker CLI install on hub side** rather than ask
  forge to bundle them: minimal scope. If future BCs find value in
  forge-managed grype/docker, open an FG story — not a reason to
  block BC-095.
- **Purge curl after the static fetches**: the forge builder base
  ships `tar` and `ca-certificates` but not `curl`. Installing curl
  solely to run the grype + docker install scripts is a build-time
  tool; keeping it in the builder layer wastes ~5 MB that doesn't
  survive the multi-stage COPY but also serves no purpose. Purge is
  mechanical, zero cost.
- **`:latest` default, no pinning yet**: forge rebuilds daily; the
  daily cadence is the update strategy. Operators who want
  reproducible rebuilds can pin via a Dockerfile ARG in a future BC
  (`ARG BUILDER_TAG=latest`). Noted as future work.

## Breaking changes

### API / return shape
**None.** HTTP contracts, callback_data, audit event names unchanged.

### Data model
**None.** No schema changes.

### Behavioral semantics
**None at runtime.** Builder stage edits only.

The one observable change is the builder's base image. Operators
who run `docker history beaconry-hub:latest` + diff against a prior
build will see a different base layer sha. That's the point.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Hub builder FROM = forge-builder-trixie | unit | `test_BC_095_builder_from.py::test_hub_builder_from_forge` | ✓ |
| Scout builder FROM = forge-builder-trixie | unit | `test_BC_095_builder_from.py::test_scout_builder_from_forge` | ✓ |
| No upstream `python:` FROM remains | unit | `test_BC_095_builder_from.py::test_no_upstream_python_from` | ✓ |
| Hub still installs grype | unit | `test_BC_095_builder_from.py::test_hub_still_installs_grype` | ✓ |
| Hub still installs docker CLI | unit | `test_BC_095_builder_from.py::test_hub_still_installs_docker_cli` | ✓ |
| Hub purges curl after fetches | unit | `test_BC_095_builder_from.py::test_hub_purges_curl_after_fetches` | ✓ |
| Scout builder prelude is minimal | unit | `test_BC_095_builder_from.py::test_scout_builder_prelude_minimal` | ✓ |
| `make verify-base-images` picks up new ref | lint | `make check` passes (cache populated with new entry) | ✓ |
| Full-tier scenarios + e2e + cold-start rebuild + smoke | gate log | `make check-full` on pre-push | ✓ |
| CAP-T196 registered | lint | `make verify-traceability` | ✓ |

## Rollout

1. Merge BC-095 to beaconry `dev` → `main` via release pipeline →
   tag `v0.16.3`.
2. Local dev: `make live-rebuild` on next pull picks up the new
   builder.
3. Homelab: next `make deploy-beaconry` (BC-094 pull path) pulls the
   forge-built beaconry-hub image, which forge will rebuild off the
   new source — no homelab change needed.
4. Observability: `docker history beaconry-hub:latest | head -5`
   should name `ghcr.io/bboortz/forge-builder-trixie:latest` in the
   BUILDER layer provenance.

## Follow-up work (documented, not shipped here)

- **BC-096 candidate**: SBOM at builder stage, consumed by
  dogfood_scan + exposed at a `/sbom` endpoint. Adopts forge's
  FG-020 pattern. See `docs/plan/IDEAS.md`.
- **FG ask**: if forge decides to bundle grype + docker CLI into
  `forge-builder-trixie` too, hub's Dockerfile drops the remaining
  curl prelude entirely. Clean small BC on the beaconry side after
  that lands.

## Links

- Story: `docs/stories/BC-095-builder-forge-trixie.md`
- BC-083 (runtime migration): sibling.
- BC-094 (homelab ghcr consumption): sibling.
- CAP-T171 (`make verify-base-images`): picks up the new ref.
- Forge image labels (verified 2026-11-07):
  - `org.opencontainers.image.source` = `https://github.com/bboortz/forge`
  - `org.opencontainers.image.revision` = `efee1310…`
  - `org.opencontainers.image.description` = "Shared builder for
    Forge scratch images — apt-upgraded + CA certs + syft"
