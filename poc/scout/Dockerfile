# syntax=docker/dockerfile:1
# BC-078 → BC-082 → BC-083 → BC-095 → BC-099: multi-stage build with
# self-owned forge builder AND forge runtime, plus a BuildKit ABI guard
# that refuses to produce an image if the two forge bases disagree on
# Python minor. Requires BuildKit (enforced by the syntax directive
# above; Makefile also exports DOCKER_BUILDKIT=1).
# Scout's job: syft a tree, POST SBOM to hub. No grype, no docker-CLI
# writes — only read access to the Docker socket (compose mount is :ro).

# ── Stage 0: runtime base reference (BC-099 ABI guard source) ──────────────
# Named FROM so the ABI guard's `--mount=from=runtime-src` resolves to the
# same digest BuildKit uses for the Stage 2 runtime FROM.
FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src

# ── Stage 1: builder ────────────────────────────────────────────────────────
# BC-095: forge-builder-trixie ships Python + pip + syft + CA certs.
# Python minor is detected at build time (BC-098) and checked against
# the runtime base (BC-099). Scout needs exactly that set — no extra
# RUN steps.
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

# BC-098 (pattern) + BC-099 (ABI guard). See hub/Dockerfile for the
# full rationale + failure wording. Mounts the runtime base's
# /usr/local/lib via BuildKit, reads the runtime Python minor from
# the directory name, asserts it matches the builder, then does
# BC-098's mv. Order: AFTER the `syft dir:/install` step so the SBOM
# captures wheels first.
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
      echo "during a Python minor bump. Remedy: pull both bases fresh + rebuild." >&2; \
      echo "See docs/stories/BC-099-base-abi-guard.md." >&2; \
      exit 1; \
    fi \
 && mkdir -p "/stage/usr/local/lib/python${BUILDER_PY_VER}" \
 && mv "/install/lib/python${BUILDER_PY_VER}/site-packages" \
       "/stage/usr/local/lib/python${BUILDER_PY_VER}/site-packages"

# ── Stage 2: runtime (forge) ────────────────────────────────────────────────
# ghcr.io/bboortz/python-scratch-trixie:latest — same forge base as hub.
# See hub/Dockerfile for the baked-in guarantees (uid 65532, ENTRYPOINT,
# SSL_CERT_FILE, nonroot passwd entry) and the BC-097 rationale for :latest.
# BC-098: /stage layout + `COPY --from=builder /stage /` — Python
# version-agnostic (see hub).
# BC-099: `FROM runtime-src` reuses the resolved digest from Stage 0 above
# — the ABI guard's `--mount=from=runtime-src` reads the exact same image.
FROM runtime-src AS runtime

COPY --from=builder /stage /

COPY --from=builder /usr/local/bin/syft /usr/local/bin/syft

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app

# BC-096: baked-in SBOM. Served at GET /sbom + consumed by
# `make dogfood-sbom` (`grype sbom:/sbom.json`).
COPY --from=builder /sbom.json /sbom.json

ENV PYTHONUNBUFFERED=1

# BC-099: bake a HEALTHCHECK into the image itself so operators doing
# `docker inspect <image>` or standalone `docker run` see a populated
# `.State.Health.Status`. Compose healthchecks (BC-097) override when
# set. Matches BC-097's compose shape + interval.
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD ["/hc", "-t", "3", "http://localhost:8001/readyz"]

# USER 65532:65532 + ENTRYPOINT /usr/local/bin/python3 come from the base image.

EXPOSE 8001
CMD ["scout.py"]
