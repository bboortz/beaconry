# syntax=docker/dockerfile:1
# BC-100 — telegram_mock onto the forge-signed scratch chain.
# Mirrors the BC-083 → BC-095 → BC-097 → BC-098 → BC-099 pattern used
# by hub + scout: multi-stage with forge-builder-trixie + python-scratch
# -trixie, BuildKit ABI guard, Python-minor-agnostic staging, baked
# HEALTHCHECK. Requires BuildKit (syntax directive above enforces it;
# Makefile exports DOCKER_BUILDKIT=1 + COMPOSE_DOCKER_CLI_BUILD=1).

# ── Stage 0: runtime base reference (BC-099 ABI guard source) ──────────────
# Named FROM so the ABI guard's `--mount=from=runtime-src` resolves to the
# same digest BuildKit uses for the Stage 2 runtime FROM.
FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src

# ── Stage 1: builder ────────────────────────────────────────────────────────
# forge-builder-trixie ships Python + pip + CA certs. Python minor detected
# at build time (BC-098) and checked against the runtime base (BC-099).
FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder

WORKDIR /app
COPY telegram_mock/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY telegram_mock/mock_bot.py ./
COPY beaconry_health/ ./beaconry_health/
COPY VERSION ./VERSION

# BC-098 (pattern) + BC-099 (ABI guard). See poc/hub/Dockerfile for the
# full rationale + failure wording. Mounts the runtime base's
# /usr/local/lib via BuildKit, reads the runtime Python minor from
# the directory name, asserts it matches the builder, then does
# BC-098's mv.
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
# Same scratch base as hub + scout. `FROM runtime-src` reuses the resolved
# digest from Stage 0 — the ABI guard's `--mount=from=runtime-src` reads
# the exact same image.
FROM runtime-src AS runtime

# BC-098/BC-099: single staged COPY. /stage contains
# /usr/local/lib/python${PY_VER}/site-packages — see builder comment.
COPY --from=builder /stage /

# Strip the /sbom.json that python-scratch-trixie bakes in (forge's FG-020
# contract). telegram_mock is an externally-sourced test fixture; forge's
# FG-020 acceptance test asserts that externally-sourced images do NOT
# carry an embedded SBOM (they go through the image-direct scan fallback).
# BC-100's move to python-scratch-trixie inadvertently inherited the base's
# SBOM, breaking FG-020 + FG-032. Remove it to restore the invariant.
# Base runs as uid 65532; temporarily switch to root to unlink, then back.
USER 0
RUN ["/usr/local/bin/python3", "-c", "import os; os.path.exists('/sbom.json') and os.unlink('/sbom.json')"]
USER 65532:65532

WORKDIR /app
COPY --from=builder --chown=65532:65532 /app /app

ENV PYTHONUNBUFFERED=1

# BC-099: bake a HEALTHCHECK into the image itself so `docker inspect`
# + standalone `docker run` see a populated `.State.Health.Status`.
# Points at /readyz via the shared beaconry_health router (BC-012).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/hc", "-t", "3", "http://localhost:8080/readyz"]

EXPOSE 8080
# Scratch base bakes ENTRYPOINT ["/usr/local/bin/python3"], so CMD is
# the arg list passed to python3. Was `python3 -u -m uvicorn ...`
# pre-BC-100; now just the args.
CMD ["-u", "-m", "uvicorn", "mock_bot:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
