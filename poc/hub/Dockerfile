# BC-078: multi-stage build. Builder keeps apt + curl to install syft,
# grype, docker CLI, pip deps; runtime is distroless (no shell, no apt,
# no coreutils). Operator can no longer `docker exec /bin/sh`.

# ── Stage 1: builder ────────────────────────────────────────────────────────
# python 3.11 — matches the distroless runtime's bundled interpreter so
# pip-installed wheels are compatible.
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# syft (post-fix re-verification) + grype (CVE matching).
RUN curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
    | sh -s -- -b /usr/local/bin
RUN curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \
    | sh -s -- -b /usr/local/bin

# BC-009: docker CLI so DockerAdapter.execute can run `docker tag`/`docker pull`
# against the host daemon via the mounted socket. Static binary; pinned.
# 2026-11-03: bumped 27.5.1 → 29.9.2 to pick up Go stdlib ≥1.26.9. The 27.5.1
# release was built with go1.22.11, which GO-2026-6604 (Windows path-traversal
# in os.Root.Mkdir) flags as vulnerable. Not reachable from a Linux container,
# but beaconry's own alerts kept firing on itself — fix at the source.
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

# ── Stage 2: runtime (distroless) ───────────────────────────────────────────
# gcr.io/distroless/python3-debian12:nonroot — python 3.11, no shell, no apt;
# baked-in USER nonroot (uid 65532, gid 65532). Docker-socket access comes
# from compose `group_add` (BC-078), not an in-image groupadd.
FROM gcr.io/distroless/python3-debian12:nonroot AS runtime

# Python deps installed by the builder (pure-python + wheels built for
# 3.11, which matches runtime 3.11).
COPY --from=builder /install/lib/python3.11/site-packages /usr/lib/python3.11/dist-packages

# Native binaries → /usr/bin (default PATH entry in distroless).
COPY --from=builder /usr/local/bin/syft /usr/bin/syft
COPY --from=builder /usr/local/bin/grype /usr/bin/grype
COPY --from=builder /usr/local/bin/docker /usr/bin/docker

WORKDIR /app
COPY --from=builder --chown=nonroot:nonroot /app /app
# Fresh named volumes inherit this dir's ownership (65532:65532).
# Operators upgrading from BC-077 (uid 1000) run the one-time chown in CHANGELOG.
COPY --from=builder --chown=nonroot:nonroot /stage/data /data

# USER nonroot + ENTRYPOINT /usr/bin/python3 are baked into the base image.
ENV PYTHONUNBUFFERED=1 PATH=/usr/bin:/bin

CMD ["hub.py"]
