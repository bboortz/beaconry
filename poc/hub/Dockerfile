# BC-078 → BC-082 → BC-083 → BC-095: multi-stage build with a self-owned
# forge builder AND forge runtime. Both bases are cosign-signed keyless-OIDC
# by forge/build.yml@main.
#
# ── Stage 1: builder ────────────────────────────────────────────────────────
# BC-095: forge-builder-trixie ships python 3.13.18 (ABI-matches the runtime),
# pip, syft, CA certs, tar. Extends BC-083's runtime migration one stage up.
# Still-missing tooling installed below: curl (build-time only), grype, docker CLI.
FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder

# BC-095: forge-builder-trixie has no `curl` — apt it in temporarily for the
# two static fetches below (grype + docker CLI), then purge to keep the
# builder layer slim. `ca-certificates` + `tar` are already baked in.
#
# BC-009: docker CLI so DockerAdapter.execute can run `docker tag`/`docker pull`
# against the host daemon via the mounted socket. Static binary; pinned.
# BC-081 (2026-11-03): bumped 27.5.1 → 29.9.2 to pick up Go stdlib ≥1.26.9.
ARG DOCKER_CLI_VERSION=29.9.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \
      | sh -s -- -b /usr/local/bin \
 && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz -C /tmp \
 && mv /tmp/docker/docker /usr/local/bin/docker \
 && rm -rf /tmp/docker \
 && docker --version \
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

# ── Stage 2: runtime (forge) ────────────────────────────────────────────────
# ghcr.io/bboortz/python-scratch-trixie:3.13 — self-owned forge image.
# Python 3.13.18, PATH=/usr/local/bin, baked USER 65532:65532 +
# ENTRYPOINT /usr/local/bin/python3 + SSL_CERT_FILE + /etc/passwd entry
# for nonroot. No shell, no apt, no coreutils. BC-083 pre-flight #2 verified.
FROM ghcr.io/bboortz/python-scratch-trixie:3.13 AS runtime

# Python deps — forge sys.path is /usr/local/lib/python3.13/*, not /usr/lib/*.
COPY --from=builder /install/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# Native binaries → /usr/local/bin (forge PATH).
COPY --from=builder /usr/local/bin/syft /usr/local/bin/syft
COPY --from=builder /usr/local/bin/grype /usr/local/bin/grype
COPY --from=builder /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app
# Fresh named volumes inherit /data's ownership (65532:65532 preserved
# from BC-082 → zero chown migration for upgrading operators).
COPY --from=builder --chown=65532:65532 /stage/data /data

# BC-096: baked-in SBOM. Served at GET /sbom + consumed by
# `make dogfood-sbom` (`grype sbom:/sbom.json`).
COPY --from=builder /sbom.json /sbom.json

ENV PYTHONUNBUFFERED=1
# USER 65532:65532 + ENTRYPOINT /usr/local/bin/python3 come from the base image.
CMD ["hub.py"]
