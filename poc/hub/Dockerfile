FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install syft (for post-fix re-verification) and grype (for CVE matching)
RUN curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
    | sh -s -- -b /usr/local/bin
RUN curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \
    | sh -s -- -b /usr/local/bin

# BC-009: install docker CLI so DockerAdapter.execute can run `docker tag` + `docker pull`
# against the host daemon via the mounted socket. Static binary; pinned for reproducibility.
ARG DOCKER_CLI_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xz -C /tmp && \
    mv /tmp/docker/docker /usr/local/bin/docker && \
    rm -rf /tmp/docker && \
    docker --version

WORKDIR /app
COPY hub/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hub/hub.py hub/waterfall.py hub/urgency.py hub/kev.py ./
COPY hub/executor/ executor/
# BC-012: shared health-check package — every container COPYs this.
COPY beaconry_health/ ./beaconry_health/

CMD ["python3", "-u", "hub.py"]
