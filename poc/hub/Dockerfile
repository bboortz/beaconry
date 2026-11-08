# syntax=docker/dockerfile:1
# BC-078 → BC-082 → BC-083 → BC-095 → BC-099: multi-stage build with
# self-owned forge builder AND forge runtime, plus a BuildKit ABI guard
# that refuses to produce an image if the two forge bases disagree on
# Python minor. Requires BuildKit (enforced by the syntax directive
# above; Makefile also exports DOCKER_BUILDKIT=1 for belt-and-braces).
#
# ── Stage 0a: grype source (Docker Hub) ─────────────────────────────────────
# Pull grype's static Go binary from Docker Hub instead of GitHub releases.
# GitHub's releases/download/* CDN flaps 504s during traffic spikes
# (observed 2026-11-08: ≥6-min outages that exhausted curl --retry). Docker
# Hub is a separate CDN that stayed healthy during the same windows.
# Forge's own CI made the same pivot for syft/grype/cosign/hadolint.
FROM anchore/grype:latest AS grype-src

# ── Stage 0b: runtime base reference (BC-099 ABI guard source) ─────────────
# Named FROM so the ABI guard's `--mount=from=runtime-src` resolves to the
# same digest BuildKit will use for the Stage 2 runtime FROM. Without
# this named handle, BuildKit could theoretically re-resolve the
# `:latest` tag between the mount and the FROM — rare but possible,
# and exactly the race class BC-099 is defending against.
FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src

# ── Stage 1: builder ────────────────────────────────────────────────────────
# BC-095: forge-builder-trixie ships Python + pip + syft + CA certs + tar.
# Python minor is detected at build time (BC-098) and matches runtime by
# forge contract (both images bumped as a pair). Extends BC-083's runtime
# migration one stage up.
FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder

# grype — pulled from the stage above, no GitHub release CDN dependency.
COPY --from=grype-src /grype /usr/local/bin/grype

# BC-009: docker CLI so DockerAdapter.execute can run `docker tag`/`docker pull`
# against the host daemon via the mounted socket. Static binary; pinned.
# BC-081 (2026-11-03): bumped 27.5.1 → 29.9.2 to pick up Go stdlib ≥1.26.9.
#
# curl is still needed for the docker CLI tarball (download.docker.com, not
# GitHub). apt it in temporarily, then purge to keep the builder layer slim.
ARG DOCKER_CLI_VERSION=29.9.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz -C /tmp \
 && mv /tmp/docker/docker /usr/local/bin/docker \
 && rm -rf /tmp/docker \
 && docker --version \
 && grype --version \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY hub/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY hub/hub.py hub/waterfall.py hub/urgency.py hub/kev.py hub/audit.py ./
COPY hub/executor/ executor/
COPY hub/notifiers/ notifiers/
COPY beaconry_health/ ./beaconry_health/
COPY VERSION ./VERSION

# BC-096: baked-in SBOM. syft (from forge-builder-trixie, BC-095) catalogs
# the python wheels in /install → /sbom.json, which the runtime stage
# COPYs in below. Adopts forge's FG-020 pattern: a frozen SBOM travels
# with the image so `grype sbom:/sbom.json` separates "image regressed"
# from "grype DB learned a new CVE" (image-scan dogfood path stays as
# the complementary Go-stdlib-aware lens).
RUN syft dir:/install -o syft-json=/sbom.json

# Pre-create /data with the nonroot (65532) owner so the runtime COPY
# below produces a correctly-owned mountpoint for fresh volumes.
RUN mkdir -p /stage/data && chown -R 65532:65532 /stage/data

# BC-098 (pattern) + BC-099 (ABI guard). Mounts the runtime base's
# /usr/local/lib via BuildKit `--mount=from=runtime-src`, reads the
# runtime's Python minor from the directory name (no binary execution),
# asserts it matches the builder's, then runs BC-098's mv. On mismatch:
# non-zero exit with the BC-099 marker + remedy. Subsumes BC-098's
# standalone PY_VER step (single RUN, single layer). Order: AFTER the
# `syft dir:/install` step above so the SBOM captures wheels first.
RUN --mount=from=runtime-src,source=/usr/local/lib,target=/runtime-lib \
    BUILDER_PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
 && RUNTIME_PY_DIR_COUNT="$(ls /runtime-lib | grep -cE '^python3\.[0-9]+$')" \
 && if [ "$RUNTIME_PY_DIR_COUNT" != "1" ]; then \
      echo "BC-099: expected exactly one python3.X dir in python-scratch-trixie:latest, found $RUNTIME_PY_DIR_COUNT" >&2; \
      ls -la /runtime-lib >&2; exit 1; \
    fi \
 && RUNTIME_PY_VER="$(ls /runtime-lib | grep -E '^python3\.[0-9]+$' | sed 's/^python//')" \
 && if [ "$BUILDER_PY_VER" != "$RUNTIME_PY_VER" ]; then \
      echo "" >&2; \
      echo "BC-099 ABI guard tripped: forge base Python minors disagree." >&2; \
      echo "  forge-builder-trixie:latest  -> Python ${BUILDER_PY_VER}" >&2; \
      echo "  python-scratch-trixie:latest -> Python ${RUNTIME_PY_VER}" >&2; \
      echo "" >&2; \
      echo "Cause: forge republished one base's :latest tag before the other" >&2; \
      echo "during a Python minor bump. Proceeding would ship wheels at" >&2; \
      echo "/usr/local/lib/python\${BUILDER_PY_VER}/site-packages/, which the" >&2; \
      echo "runtime's Python \${RUNTIME_PY_VER} interpreter would not find on" >&2; \
      echo "sys.path." >&2; \
      echo "" >&2; \
      echo "Remedy: wait for forge's next cron rebuild to republish the lagging" >&2; \
      echo "base, then:" >&2; \
      echo "  docker pull ghcr.io/bboortz/forge-builder-trixie:latest" >&2; \
      echo "  docker pull ghcr.io/bboortz/python-scratch-trixie:latest" >&2; \
      echo "  <re-run the build>" >&2; \
      echo "" >&2; \
      echo "See docs/stories/BC-099-base-abi-guard.md." >&2; \
      exit 1; \
    fi \
 && mkdir -p "/stage/usr/local/lib/python${BUILDER_PY_VER}" \
 && mv "/install/lib/python${BUILDER_PY_VER}/site-packages" \
       "/stage/usr/local/lib/python${BUILDER_PY_VER}/site-packages"

# ── Stage 2: runtime (forge) ────────────────────────────────────────────────
# ghcr.io/bboortz/python-scratch-trixie:latest — self-owned forge image.
# PATH=/usr/local/bin, baked USER 65532:65532 + ENTRYPOINT
# /usr/local/bin/python3 + SSL_CERT_FILE + /etc/passwd entry for nonroot.
# No shell, no apt, no coreutils. BC-083 pre-flight #2 verified.
# BC-097 (2026-11-08): bumped `:3.13` → `:latest` for symmetry with the
# BC-095 builder and to pick up FG-031's `/hc` healthcheck binary.
# BC-098 (2026-11-08): retired the "hardcoded python3.13 breaks on
# forge Python bump" risk documented here pre-BC-098 — see CAP-T199
# → CAP-T200.
# BC-099 (2026-11-08): `FROM runtime-src` reuses the resolved digest
# from the Stage 0b named reference above — the ABI guard's
# `--mount=from=runtime-src` reads from the exact same image.
FROM runtime-src AS runtime

# BC-098: single staged COPY. /stage contains /data (chown 65532:65532)
# + /usr/local/lib/python${PY_VER}/site-packages — see the builder-stage
# comment above for the rationale. Ownership preserved from the builder.
COPY --from=builder /stage /

# Native binaries → /usr/local/bin (forge PATH).
COPY --from=builder /usr/local/bin/syft /usr/local/bin/syft
COPY --from=builder /usr/local/bin/grype /usr/local/bin/grype
COPY --from=builder /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app

# BC-096: baked-in SBOM. Served at GET /sbom + consumed by
# `make dogfood-sbom` (`grype sbom:/sbom.json`).
COPY --from=builder /sbom.json /sbom.json

ENV PYTHONUNBUFFERED=1

# BC-099: bake a HEALTHCHECK into the image itself so operators doing
# `docker inspect <image>` or standalone `docker run` (no compose) see
# a populated `.State.Health.Status`. Compose healthchecks (BC-097)
# OVERRIDE this when set, so no change for the composed path. Matches
# BC-097's compose shape (`/hc -t 3` against /readyz) and BC-097's
# interval/timeout/start_period.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
  CMD ["/hc", "-t", "3", "http://localhost:8000/readyz"]

# USER 65532:65532 + ENTRYPOINT /usr/local/bin/python3 come from the base image.
CMD ["hub.py"]
