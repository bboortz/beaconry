# BC-078 → BC-082 → BC-083 → BC-095: multi-stage build with self-owned forge
# builder AND runtime. Both bases are cosign-signed keyless-OIDC by
# forge/build.yml@main.
# Scout's job: syft a tree, POST SBOM to hub. No grype, no docker-CLI
# writes — only read access to the Docker socket (compose mount is :ro).

# ── Stage 1: builder ────────────────────────────────────────────────────────
# BC-095: forge-builder-trixie ships python 3.13.18 (ABI-matches the runtime),
# pip, syft, CA certs. Scout needs exactly that set — no extra RUN steps.
FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder

WORKDIR /app
COPY scout/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY scout/scout.py ./
COPY beaconry_health/ ./beaconry_health/
COPY VERSION ./VERSION

# BC-096: baked-in SBOM. See poc/hub/Dockerfile for the rationale
# (forge FG-020 pattern — frozen SBOM separates image-regressed from
# grype-DB-learned signals).
RUN syft dir:/install -o syft-json=/sbom.json

# ── Stage 2: runtime (forge) ────────────────────────────────────────────────
# ghcr.io/bboortz/python-scratch-trixie:3.13 — same forge base as hub.
# See hub/Dockerfile for the baked-in guarantees (uid 65532, ENTRYPOINT,
# SSL_CERT_FILE, nonroot passwd entry).
FROM ghcr.io/bboortz/python-scratch-trixie:3.13 AS runtime

COPY --from=builder /install/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

COPY --from=builder /usr/local/bin/syft /usr/local/bin/syft

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app

# BC-096: baked-in SBOM. Served at GET /sbom + consumed by
# `make dogfood-sbom` (`grype sbom:/sbom.json`).
COPY --from=builder /sbom.json /sbom.json

ENV PYTHONUNBUFFERED=1
# USER 65532:65532 + ENTRYPOINT /usr/local/bin/python3 come from the base image.

EXPOSE 8001
CMD ["scout.py"]
