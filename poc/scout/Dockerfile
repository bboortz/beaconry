FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install syft for SBOM generation
RUN curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
    | sh -s -- -b /usr/local/bin

WORKDIR /app
COPY scout/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scout/scout.py ./
# BC-012: shared health-check package — every container COPYs this.
COPY beaconry_health/ ./beaconry_health/
# BC-062: single source of truth for software version.
COPY VERSION ./VERSION

# BC-077: non-root hardening. Scout only needs read access to the Docker
# socket, so the beaconry user joins the docker group. See hub/Dockerfile
# for the DOCKER_GID per-distro override notes. Scout has no persistent
# data directory, so no /data chown needed.
ARG DOCKER_GID=999
RUN groupadd -g ${DOCKER_GID} docker 2>/dev/null || groupmod -g ${DOCKER_GID} docker 2>/dev/null || true \
    && useradd -m -u 1000 -g users -G docker beaconry \
    && chown -R beaconry:users /app

USER beaconry

EXPOSE 8001
CMD ["python3", "-u", "scout.py"]
