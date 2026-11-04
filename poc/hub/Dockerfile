# BC-078 → BC-082 → BC-083: multi-stage build with a self-owned forge
# runtime (no shell, no apt, baked USER 65532 + ENTRYPOINT).
#
# ── Stage 1: builder ────────────────────────────────────────────────────────
# python 3.13-slim-trixie — matches the forge runtime's bundled interpreter
# (python3.13.18) so pip-installed wheels are ABI-compatible.
FROM python:3.13-slim-trixie AS builder

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# syft (post-fix re-verification) + grype (CVE matching).
RUN curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
    | sh -s -- -b /usr/local/bin
RUN curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \
    | sh -s -- -b /usr/local/bin

# BC-009: docker CLI so DockerAdapter.execute can run `docker tag`/`docker pull`
# against the host daemon via the mounted socket. Static binary; pinned.
# BC-081 (2026-11-03): bumped 27.5.1 → 29.9.2 to pick up Go stdlib ≥1.26.9.
ARG DOCKER_CLI_VERSION=29.9.2
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xz -C /tmp && \
    mv /tmp/docker/docker /usr/local/bin/docker && \
    rm -rf /tmp/docker && \
    docker --version

WORKDIR /app
COPY hub/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY hub/hub.py hub/waterfall.py hub/urgency.py hub/kev.py hub/audit.py ./
COPY hub/executor/ executor/
COPY hub/notifiers/ notifiers/
COPY beaconry_health/ ./beaconry_health/
COPY VERSION ./VERSION

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

# BC-083 workaround: forge image v2 is missing /usr/lib/x86_64-linux-gnu/librt.so.1.
# pydantic_core (transitive via fastapi) is a Rust wheel that NEEDS libgcc_s,
# librt, libpthread, libdl, libc — all four of the others are in the forge
# image; only librt is stripped. On glibc 2.34+ librt is a stub symlink to
# libc but the file must still exist for compiled binaries that resolve it
# by name. Remove this line once forge image v3 ships with librt restored.
COPY --from=builder /usr/lib/x86_64-linux-gnu/librt.so.1 /usr/lib/x86_64-linux-gnu/librt.so.1

# Native binaries → /usr/local/bin (forge PATH).
COPY --from=builder /usr/local/bin/syft /usr/local/bin/syft
COPY --from=builder /usr/local/bin/grype /usr/local/bin/grype
COPY --from=builder /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app
# Fresh named volumes inherit /data's ownership (65532:65532 preserved
# from BC-082 → zero chown migration for upgrading operators).
COPY --from=builder --chown=65532:65532 /stage/data /data

ENV PYTHONUNBUFFERED=1
# USER 65532:65532 + ENTRYPOINT /usr/local/bin/python3 come from the base image.
CMD ["hub.py"]
