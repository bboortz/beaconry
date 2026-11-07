# BC-083 — Forge base image + standing preflight lint

*Branch: `feat/BC-083-forge-base-image`*
*Start: 2026-11-04*

## Pre-flight findings (recorded BEFORE Dockerfile diff — CAP-T170)

```
docker pull ghcr.io/bboortz/python-scratch-trixie:3.13
```

Pre-flight #1 (digest `sha256:567ee811…`) surfaced three
blocking gotchas (no CA-cert path, no nonroot passwd entry,
no baked USER/ENTRYPOINT) that would have forced workarounds
in beaconry's Dockerfile. Those were reported upstream and
pre-flight #2 (digest `sha256:658b2a4a…`) addresses all six:

| Field | Forge base #2 | Distroless debian13 (prior) |
|---|---|---|
| Public anon pull | ✓ (token exchange + manifest 200) | ✓ |
| Baked USER | **`65532:65532`** | nonroot (65532) |
| ENTRYPOINT | **`/usr/local/bin/python3`** | `/usr/bin/python3` |
| CMD | `None` (clean — consumers set it) | (empty) |
| PATH | `/usr/local/bin` | `/usr/bin:/bin` |
| SSL_CERT_FILE | **`/etc/ssl/certs/ca-certificates.crt`** | (unset; uses /usr/bin paths) |
| SSL_CERT_DIR | **`/etc/ssl/certs`** | (unset) |
| HTTPS from python3 | ✓ `urlopen('https://api.github.com')` → 200 | ✓ |
| `/etc/passwd` entry for 65532 | ✓ `nonroot:x:65532:65532:nonroot:/nonroot:/sbin/nologin` | ✓ |
| `/etc/group` entry for 65532 | ✓ `nonroot:x:65532` | ✓ |
| WorkingDir | `/nonroot` | (unset) |
| Python | 3.13.18 | 3.13.5 |
| sys.path site-packages | `/usr/local/lib/python3.13/site-packages` | `/usr/lib/python3.13/dist-packages` |
| `PYTHONDONTWRITEBYTECODE` | **`1`** | (unset) |
| `PYTHONFAULTHANDLER` | **`1`** | (unset) |
| /bin/sh + /bin/bash | absent | absent |
| Image size | 45 MB | 53 MB |
| cosign signed | ✓ (.sig + .att under manifest tags) | — |

**Two beaconry-side runtime-stage adjustments remain:**

1. COPY site-packages into `/usr/local/lib/python3.13/site-packages`
   (forge sys.path doesn't include `/usr/lib/python3.13/*`).
2. COPY native binaries into `/usr/local/bin/{syft,grype,docker}`
   (forge PATH is `/usr/local/bin` only).

No `USER`, `ENTRYPOINT`, or `SSL_CERT_FILE` override needed —
forge image bakes them in. uid 65532 preserved → zero chown
migration for operators already on BC-082's debian13.

## Scope

### 1. Dockerfiles — runtime stage swap

Both `poc/hub/Dockerfile` and `poc/scout/Dockerfile`:

```dockerfile
FROM ghcr.io/bboortz/python-scratch-trixie:3.13 AS runtime

# Builder-stage artifacts land where forge's sys.path + PATH find them.
COPY --from=builder /install/lib/python3.13/site-packages \
                    /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/syft  /usr/local/bin/syft
COPY --from=builder /usr/local/bin/grype /usr/local/bin/grype   # hub only
COPY --from=builder /usr/local/bin/docker /usr/local/bin/docker  # hub only

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app
COPY --from=builder --chown=65532:65532 /stage/data /data     # hub only

ENV PYTHONUNBUFFERED=1 PATH=/usr/local/bin

# Forge image is root-by-default — bake in uid 65532 to preserve
# the BC-077/BC-078 non-root contract AND match BC-082's chown target
# so operators don't need a second chown.
USER 65532
ENTRYPOINT ["/usr/local/bin/python3", "-u"]
CMD ["hub.py"]   # or scout.py for scout
```

### 2. verify_pinned_versions.py — soft migration

Accept both the forge tag (primary) and distroless debian12/13
(deprecated, migration window). Named constants:

```python
RUNTIME_IMAGE_PREFERRED = "ghcr.io/bboortz/python-scratch-trixie:3.13"
RUNTIME_IMAGE_RE = re.compile(
    r"^FROM\s+(?:"
    r"ghcr\.io/bboortz/python-scratch-trixie:3\.13"  # BC-083 primary
    r"|gcr\.io/distroless/python3-debian1[23]:nonroot"  # BC-078/BC-082 fallback
    r")\s+AS\s+runtime\b",
    re.MULTILINE,
)
```

Error message names the preferred tag + the one-release deprecation
notice.

### 3. make verify-base-images — standing preflight lint (CAP-T171)

New `poc/scripts/verify_base_images.py`:
- Enumerates every `FROM` across `poc/**/Dockerfile*`.
- For each unique image:tag, extracts registry + repo + tag.
- Resolves registry → token URL (ghcr.io, gcr.io, docker.io
  conventions).
- Requests anonymous bearer token via GET
  `<registry>/token?scope=repository:<repo>:pull`.
- Fetches manifest with that token + OCI Accept headers.
- Fails if token issuance fails OR manifest returns 401/403/404/5xx.
- Caches successful results in
  `.beaconry-cache/verify-base-images.json` with 24h TTL.
- Wired into `make check` fast tier after `verify-pinned-versions`.

Operator opt-out: `BEACONRY_SKIP_BASE_IMAGES=1` (dev-only
emergency, e.g. air-gapped CI without registry reachability).

### 4. Tests

`poc/tests/integration/test_BC_083_forge_image.py` with these
assertions:
- Hub Dockerfile runtime FROM is the forge tag (string grep).
- Scout Dockerfile runtime FROM is the forge tag.
- Dockerfile-level: `USER 65532` present in both stages' runtime.
- Dockerfile-level: `ENTRYPOINT ["/usr/local/bin/python3", …]` present.
- site-packages COPY targets `/usr/local/lib/python3.13/site-packages`.
- verify_pinned_versions accepts forge tag (unit).
- verify_pinned_versions still accepts distroless debian13 (soft migration, unit).
- verify_pinned_versions rejects arbitrary ghcr repo (unit).
- verify_base_images parses FROM lines correctly (unit).
- verify_base_images caches successful probes (unit, tmp_path).
- verify_base_images honors BEACONRY_SKIP_BASE_IMAGES (unit).

Plus **live runtime** tests under `@pytest.mark.cold_start` (reuse
`test_BC_082_cold_start.py` plus new file
`test_BC_083_forge_runtime.py`):
- `docker exec beaconry-cold-start-hub /bin/sh` fails.
- `docker exec … /usr/local/bin/python3 -c "print(os.getuid())"`
  prints 65532.
- `/usr/local/bin/grype --version` works.
- `/usr/local/bin/syft --version` works.
- `/usr/local/bin/docker --version` works.

### 5. BC-078 string-grep test adjustment

`test_BC_078_distroless.py::test_hub_dockerfile_uses_distroless_runtime`
is false-by-design once we land this. Loosen to "uses an
accepted scratch/distroless runtime" and point verified_by at
BC-083 for the primary-forge assertion.

### 6. CHANGELOG + CI-FAILURES

CHANGELOG v0.10.0 entry:
- Base image provider swap ghcr → distroless → ghcr (self-owned forge).
- Preflight lint added.
- No upgrade step needed (uid 65532 preserved).

CI-FAILURES.md: no entry needed yet — but if a future base-image
provider vanishes and verify-base-images catches it, that's the
BC-084 entry.

### Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Hub Dockerfile runtime FROM = forge tag | integration | `test_BC_083_forge_image.py::test_hub_runtime_is_forge` | ✓ |
| Scout Dockerfile runtime FROM = forge tag | integration | `test_BC_083_forge_image.py::test_scout_runtime_is_forge` | ✓ |
| Runtime stage has USER 65532 | integration | `test_BC_083_forge_image.py::test_runtime_user_is_65532` | ✓ |
| Runtime stage has ENTRYPOINT /usr/local/bin/python3 | integration | `test_BC_083_forge_image.py::test_runtime_entrypoint_is_python3` | ✓ |
| site-packages COPY targets /usr/local/lib/python3.13 | integration | `test_BC_083_forge_image.py::test_runtime_site_packages_path` | ✓ |
| Pinned-version lint accepts forge tag | unit | `test_BC_083_forge_image.py::test_pinned_versions_accepts_forge_tag` | ✓ |
| Pinned-version lint still accepts distroless debian13 (soft migration) | unit | `test_BC_083_forge_image.py::test_pinned_versions_accepts_distroless_fallback` | ✓ |
| Pinned-version lint rejects arbitrary ghcr repo | unit | `test_BC_083_forge_image.py::test_pinned_versions_rejects_arbitrary_ghcr` | ✓ |
| verify_base_images parses FROM lines | unit | `test_BC_083_forge_image.py::test_verify_base_images_parses_froms` | ✓ |
| verify_base_images caches successful probes | unit | `test_BC_083_forge_image.py::test_verify_base_images_cache_hit` | ✓ |
| verify_base_images honors BEACONRY_SKIP_BASE_IMAGES | unit | `test_BC_083_forge_image.py::test_verify_base_images_skip_env` | ✓ |
| verify_base_images wired into make check | integration | `test_BC_083_forge_image.py::test_make_check_runs_verify_base_images` | ✓ |
| Cold-start runtime: no shell inside hub | cold_start | `test_BC_083_forge_runtime.py::test_hub_has_no_shell` | ✓ |
| Cold-start runtime: uid 65532 | cold_start | `test_BC_083_forge_runtime.py::test_hub_runs_as_65532` | ✓ |
| Cold-start runtime: grype binary works | cold_start | `test_BC_083_forge_runtime.py::test_hub_grype_binary_runs` | ✓ |
| Cold-start runtime: syft binary works (scout) | cold_start | `test_BC_083_forge_runtime.py::test_scout_syft_binary_runs` | ✓ |
| Cold-start runtime: all hub deps import (catches missing .so) | cold_start | `test_BC_083_forge_runtime.py::test_hub_can_import_all_runtime_deps` | ✓ |
| Cold-start runtime: all scout deps import | cold_start | `test_BC_083_forge_runtime.py::test_scout_can_import_all_runtime_deps` | ✓ |
| Cold-start all 8 endpoints green (reused) | cold_start | `test_BC_082_cold_start.py::test_cold_start_all_eight_endpoints` | ✓ |
| Dogfood zero High+ fixable | multi-gate | `make dogfood-scan` log | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

- **None for operators** who already migrated to BC-082's debian13
  (uid 65532 preserved via explicit `USER 65532` directive).
- First `make live-rebuild` pulls the new base (~1 min).
- Operators on pre-BC-082 debian12 still accepted during migration
  window.

## Version

0.9.0 → 0.10.0 (minor — new base-image provider + new standing
lint; no API/schema/behavior change).
