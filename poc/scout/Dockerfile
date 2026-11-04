# BC-078 → BC-082 → BC-083: multi-stage build with self-owned forge runtime.
# Scout's job: syft a tree, POST SBOM to hub. No grype, no docker-CLI
# writes — only read access to the Docker socket (compose mount is :ro).

# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.13-slim-trixie AS builder

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# syft — SBOM generator.
RUN curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
    | sh -s -- -b /usr/local/bin

WORKDIR /app
COPY scout/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY scout/scout.py ./
COPY beaconry_health/ ./beaconry_health/
COPY VERSION ./VERSION

# ── Stage 2: runtime (forge) ────────────────────────────────────────────────
# ghcr.io/bboortz/python-scratch-trixie:3.13 — same forge base as hub.
# See hub/Dockerfile for the baked-in guarantees (uid 65532, ENTRYPOINT,
# SSL_CERT_FILE, nonroot passwd entry).
FROM ghcr.io/bboortz/python-scratch-trixie:3.13 AS runtime

COPY --from=builder /install/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

COPY --from=builder /usr/local/bin/syft /usr/local/bin/syft

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app

ENV PYTHONUNBUFFERED=1
# USER 65532:65532 + ENTRYPOINT /usr/local/bin/python3 come from the base image.

EXPOSE 8001
CMD ["scout.py"]
