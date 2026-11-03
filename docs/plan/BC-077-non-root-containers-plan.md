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

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Hub Dockerfile declares non-root USER | unit | `test_BC_077_non_root.py::test_hub_dockerfile_has_user_directive` | ✓ |
| Scout Dockerfile declares non-root USER | unit | `test_BC_077_non_root.py::test_scout_dockerfile_has_user_directive` | ✓ |
| Hub Dockerfile creates beaconry user with explicit uid | unit | `test_BC_077_non_root.py::test_hub_dockerfile_creates_beaconry_user` | ✓ |
| Scout Dockerfile creates beaconry user with explicit uid | unit | `test_BC_077_non_root.py::test_scout_dockerfile_creates_beaconry_user` | ✓ |
| Both Dockerfiles pass DOCKER_GID build-arg | integration | `test_BC_077_non_root.py::test_dockerfiles_accept_docker_gid_arg` | ✓ |
| groupadd uses `${DOCKER_GID}` so operators can override | integration | `test_BC_077_non_root.py::test_dockerfiles_groupadd_uses_docker_gid` | ✓ |
| /app + /data chowned to beaconry in image | integration | `test_BC_077_non_root.py::test_dockerfiles_chown_app_and_data` | ✓ |
| docker-compose propagates DOCKER_GID (hub) | integration | `test_BC_077_non_root.py::test_compose_passes_docker_gid_build_arg_for_hub` | ✓ |
| docker-compose propagates DOCKER_GID (scout) | integration | `test_BC_077_non_root.py::test_compose_passes_docker_gid_build_arg_for_scout` | ✓ |
| DOCKER_GID default 999 matches Debian/Ubuntu | integration | `test_BC_077_non_root.py::test_compose_docker_gid_default_is_999` | ✓ |
| CHANGELOG documents the one-time chown step | integration | `test_BC_077_non_root.py::test_changelog_documents_the_upgrade_step` | ✓ |
| USER directive is last directive before CMD | unit | `test_BC_077_non_root.py::test_user_directive_before_cmd` | ✓ |
| DOCKER_GID row in VARIABLES.md | lint | `make verify-variables` | ✓ |
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
