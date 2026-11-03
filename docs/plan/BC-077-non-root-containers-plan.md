# BC-077 — Non-root hub + scout containers

*Branch: `feat/BC-077-non-root-containers`*
*Start: 2026-11-03*

## Why

Operator asked for own-attack-surface reduction, with the constraint
*"Beaconry should not introduce new issues"*. Running hub + scout as
root in-container plus mounting the Docker socket means a container-
side RCE escalates to root on the host. Dropping to a non-root user
caps the blast radius at "unprivileged in container" + the socket's
own permission check. Prerequisite for BC-078 (distroless).

## Scope

### Both Dockerfiles

- Add build-time user `beaconry` (uid 1000, group `users` gid 100)
  via `useradd`.
- Create a `docker` group inside the container matching the host's
  Docker socket gid; add `beaconry` to it. Build arg
  `DOCKER_GID` (default `999`, Debian/Ubuntu default) lets operators
  override for Arch (`989`), Fedora (`995`), etc.
- `chown -R beaconry:users /app /data` at build time so fresh
  volumes inherit the right ownership (Docker copies the image dir's
  mode to a fresh named volume).
- Add `USER beaconry` as the last Dockerfile directive before `CMD`.

### `poc/docker-compose.yml`

Pass `DOCKER_GID` as a build arg so operators can override once in
their `.env`:

```yaml
hub:
  build:
    context: ./
    dockerfile: hub/Dockerfile
    args:
      DOCKER_GID: ${DOCKER_GID:-999}
```

Same for scout.

### Env docs

- `docs/VARIABLES.md`: new row for `DOCKER_GID` (build-arg, not runtime).
- `poc/.env.example`: line commented out by default pointing at how to
  check the local gid.

### Upgrade path for existing deployments

Fresh deploys work out of the box: Docker initializes a new named
volume with the image dir's ownership (beaconry:users). Existing
`beaconry-data` volumes have files owned by `root:root` from the
pre-BC-077 image and the non-root hub can't write.

Documented upgrade step in `CHANGELOG.md` + a short note in
`docs/INSTALL.md`:

```bash
# One-time upgrade step for pre-BC-077 operators (fresh installs skip this):
docker run --rm --user root -v beaconry-data:/data python:3.12-slim \
    chown -R 1000:100 /data
```

### Not addressed (deferred to BC-078)

- Distroless base image + multi-stage builds — follow-up.
- Removing root from the image entirely — distroless does that cleanly.

## Shipping evidence

> **Superseded by BC-078 (2026-11-03)**: BC-077 shipped non-root as
> `useradd beaconry` (uid 1000) + `ARG DOCKER_GID` + in-image
> `groupadd`. BC-078 replaced that approach wholesale with the
> distroless base (uid 65532 baked in; `group_add` on compose
> runtime instead of in-image groupadd). The original BC-077 test
> file `test_BC_077_non_root.py` was removed because its assertions
> contradicted BC-078's structure. Non-root behavior is now verified
> by `test_BC_078_distroless.py` — see rows below. Historical plan
> text (above) is preserved as a decision record.

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Non-root hub runtime (uid 65532, baked into distroless) | integration | `test_BC_078_distroless.py::test_hub_dockerfile_uses_distroless_runtime` | ✓ |
| Non-root scout runtime (uid 65532, baked into distroless) | integration | `test_BC_078_distroless.py::test_scout_dockerfile_uses_distroless_runtime` | ✓ |
| docker-socket gid override at runtime (compose group_add replaces BC-077 build-arg) | integration | `test_BC_078_distroless.py::test_compose_uses_group_add` | ✓ |
| CHANGELOG documents volume chown for upgrading operators | integration | `test_BC_078_distroless.py::test_changelog_documents_65532_chown` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

**Yes, visible to upgrading operators**:

- Existing `beaconry-data` volume files are owned by root; the new
  non-root hub can't write. One-time `chown` step required
  (documented in CHANGELOG + INSTALL.md). Takes ~5 seconds.
- Fresh installs are unaffected.
- Operators on hosts where Docker socket gid ≠ 999 (Arch, Fedora,
  Alpine host) need to set `DOCKER_GID` in their `.env` before
  rebuilding. Default of 999 matches Debian/Ubuntu — the common case.

Documented clearly in `CHANGELOG.md` for the v0.6.1 entry.

## Version

0.6.0 → 0.6.1 (patch — hardening, not a new feature; upgrade step
is a one-off chown, not a workflow change).
