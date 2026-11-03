# BC-078: multi-stage build with distroless runtime (no shell, no apt).
# Scout's job: syft a tree, POST SBOM to hub. No grype, no docker-CLI
# writes — only read access to the Docker socket (compose mount is :ro).

# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

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

# ── Stage 2: runtime (distroless) ───────────────────────────────────────────
FROM gcr.io/distroless/python3-debian12:nonroot AS runtime

COPY --from=builder /install/lib/python3.11/site-packages /usr/lib/python3.11/dist-packages
COPY --from=builder /usr/local/bin/syft /usr/bin/syft

WORKDIR /app
COPY --from=builder --chown=nonroot:nonroot /app /app

ENV PYTHONUNBUFFERED=1 PATH=/usr/bin:/bin

EXPOSE 8001
CMD ["scout.py"]
