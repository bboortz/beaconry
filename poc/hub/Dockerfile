# BC-078 → BC-082 → BC-083 → BC-095: multi-stage build with a self-owned
# forge builder AND forge runtime. Both bases are cosign-signed keyless-OIDC
# by forge/build.yml@main.
#
# ── Stage 0: grype source (Docker Hub) ──────────────────────────────────────
# Pull grype's static Go binary from Docker Hub instead of GitHub releases.
# GitHub's releases/download/* CDN flaps 504s during traffic spikes
# (observed 2026-11-08: ≥6-min outages that exhausted curl --retry). Docker
# Hub is a separate CDN that stayed healthy during the same windows.
# Forge's own CI made the same pivot for syft/grype/cosign/hadolint.
FROM anchore/grype:latest AS grype-src

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

# BC-098: Python-minor-agnostic site-packages staging. Ask the interpreter
# for its own major.minor, then move site-packages from the pip --prefix
# layout (/install/lib/pythonX.Y/) into a staging path that mirrors the
# runtime root (/stage/usr/local/lib/pythonX.Y/). The runtime stage
# below does a single `COPY --from=builder /stage /` and site-packages
# lands at the exact path Python's native sys.path expects, with no
# hardcoded version literal anywhere. Works for any future forge Python
# bump without a beaconry PR. Order matters: this runs AFTER the
# `syft dir:/install` step above so the SBOM captures wheels before
# the move.
RUN PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
 && mkdir -p "/stage/usr/local/lib/python${PY_VER}" \
 && mv "/install/lib/python${PY_VER}/site-packages" \
       "/stage/usr/local/lib/python${PY_VER}/site-packages"

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
FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime

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
# USER 65532:65532 + ENTRYPOINT /usr/local/bin/python3 come from the base image.
CMD ["hub.py"]
