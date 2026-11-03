# BC-078 — Distroless runtime for hub + scout

*Branch: `feat/BC-078-distroless-images`*
*Start: 2026-11-03*

## Why

BC-077 landed non-root. The runtime image is still
`python:3.12-slim` — ships `apt`, `curl`, `/bin/bash`, coreutils,
~70 MB of debian base. A distroless runtime strips all of those.

## Scope

### Multi-stage Dockerfiles (hub + scout)

Stage 1 (`builder`) — `python:3.11-slim-bookworm`:
- `apt-get install curl` (needed for syft/grype installers)
- Install syft + grype to `/usr/local/bin/` (grype only in hub)
- `pip install --no-cache-dir -r requirements.txt` → system site-packages
- Also install `docker` CLI static binary from docker.com's static release URL (hub only — DockerAdapter needs the binary)

Stage 2 (`runtime`) — `gcr.io/distroless/python3-debian12:nonroot`:
- `COPY --from=builder /usr/local/lib/python3.11/dist-packages /usr/local/lib/python3.11/dist-packages`
- `COPY --from=builder /usr/local/bin/syft /usr/bin/syft` (both)
- `COPY --from=builder /usr/local/bin/grype /usr/bin/grype` (hub only)
- `COPY --from=builder /usr/local/bin/docker /usr/bin/docker` (hub only)
- `COPY . /app` with correct ownership
- `WORKDIR /app`
- `USER nonroot` (uid 65532) — baked into base
- `ENV PYTHONUNBUFFERED=1 PATH=/usr/bin:/bin`
- `ENTRYPOINT ["/usr/bin/python3"]` (baked into base image)
- `CMD ["hub.py"]` (resp. `scout.py`)

### `poc/docker-compose.yml`

Remove `build.args.DOCKER_GID`; add `group_add: ["${DOCKER_GID:-999}"]` to
both services (hub + scout both touch the socket; scout read-only via
compose mount already). This moves gid selection from build-time to
runtime — operators change `.env` without rebuilding.

### `pyproject.toml`

Drop `requires-python = ">=3.12"` → `>=3.11`. Local dev stays 3.12
(our `.venv` + the fast-tier `make check`). Runtime inside the
container is 3.11.

### `docs/VARIABLES.md`

Re-add `DOCKER_GID` row, this time correctly as a **runtime** compose
knob consumed by `group_add` (passes the orphan check because
`docker-compose.yml` reads it, and the lint's scanner only treats
`.py` files as "code").

Wait — the lint scans `.py` only. `docker-compose.yml` isn't scanned.
So the DOCKER_GID row still fails the orphan check. Add
`DOCKER_GID` to a new `COMPOSE_ONLY_ALLOWLIST` in
`verify_variables_documented.py` with justification.

### CHANGELOG

Top-of-section upgrade step for v0.7.0:

```bash
# BC-078 one-time step for operators upgrading from v0.6.x
docker run --rm --user root -v beaconry-data:/data python:3.12-slim \
    chown -R 65532:65532 /data
```

Fresh v0.7.0 installs skip this.

### Capability drop

Mark CAP-T161 as new in `docs/CAPABILITIES.md`.

### Breaking changes

Yes, enumerate explicitly:

1. **Volume ownership**: BC-077 shipped uid 1000, BC-078 ships uid
   65532 (distroless `:nonroot`). Second one-time chown required
   for operators on v0.6.1 who then jump to v0.7.0.
2. **No shell**: operators can no longer `docker exec beaconry-hub /bin/sh`
   for debugging. The replacement is logs + `make audit`/`make
   findings`/`make status` + `docker exec` with absolute binary
   paths (`/usr/bin/python3`, `/usr/bin/grype`).
3. **Python 3.12 → 3.11 at runtime**: pyproject requires-python
   drops to `>=3.11`. Operators who had pinned 3.12 for some
   reason need to re-verify (we don't do anything 3.12-only).

### Not addressed (future stories)

- `--cap-drop=ALL` + explicit capabilities per service.
- `read_only: true` on compose services — needs tmpfs plumbing.
- cosign-signed images.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Hub Dockerfile uses distroless runtime stage | integration | `test_BC_078_distroless.py::test_hub_dockerfile_uses_distroless_runtime` | ✓ |
| Scout Dockerfile uses distroless runtime stage | integration | `test_BC_078_distroless.py::test_scout_dockerfile_uses_distroless_runtime` | ✓ |
| Both Dockerfiles are multi-stage | integration | `test_BC_078_distroless.py::test_dockerfiles_are_multi_stage` | ✓ |
| Hub runtime copies syft + grype + docker binaries | integration | `test_BC_078_distroless.py::test_hub_runtime_copies_binaries` | ✓ |
| Scout runtime copies syft binary | integration | `test_BC_078_distroless.py::test_scout_runtime_copies_syft_binary` | ✓ |
| Runtime stage installs no shell | integration | `test_BC_078_distroless.py::test_dockerfiles_do_not_install_shell_in_runtime` | ✓ |
| compose uses group_add instead of build-arg DOCKER_GID | integration | `test_BC_078_distroless.py::test_compose_uses_group_add` | ✓ |
| compose no longer passes DOCKER_GID as build-arg | integration | `test_BC_078_distroless.py::test_compose_removed_docker_gid_build_arg` | ✓ |
| pyproject requires-python allows 3.11 | integration | `test_BC_078_distroless.py::test_pyproject_requires_python_allows_311` | ✓ |
| CHANGELOG documents uid 65532 chown step | integration | `test_BC_078_distroless.py::test_changelog_documents_65532_chown` | ✓ |
| CAP-T161 registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Version

0.6.1 → 0.7.0 (minor — architectural change, two breaking changes
for upgraders, visible impact on debugging workflow).
