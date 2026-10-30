---
name: docker-standard
description: Docker Compose patterns and healthcheck templates for beaconry containers
metadata:
  type: reference
---

*Adapted from homelab docker-standard.md for beaconry's Python/FastAPI stack.*

## docker-compose.yml template

```yaml
services:
  <name>:
    build: ./<name>
    image: <name>:latest
    container_name: beaconry-<name>
    restart: unless-stopped
    environment:
      - KEY=${KEY}
    volumes:
      - beaconry-data:/data
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:<port>/health')"]
      interval: 60s
      start_interval: 2s   # Docker 25+: polls every 2s during start_period — cuts smoke wait to ~5s
      timeout: 10s
      start_period: 60s    # grace period for grype db update on hub first-start
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  beaconry-data:
```

## Healthcheck patterns for beaconry services

**Point every service's compose healthcheck at `/readyz` per `memory/health-checks.md`** (the four-endpoint convention adopted BC-010). The single-`/health` style below is retained as a transitional reference only; new services must adopt `/livez` + `/readyz` + `/startupz` + `/healthz` from day one.

- **Hub (FastAPI, canonical)**: `["CMD", "python3", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/readyz').status==200 else 1)"]`
- **Scout (file-sentinel analogue)**: `["CMD-SHELL", "test -f /data/scout.ready && [ $(stat -c %Y /data/scout.live) -gt $(( $(date +%s) - 300 )) ]"]`
- **Legacy single `/health` (hub, pre-BC-010)**: `["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]`
- **Legacy process check (do not use for new services)**: `["CMD-SHELL", "pgrep -f scout.py > /dev/null"]`

## Dockerfile template (Python service)

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python3", "-u", "main.py"]
```

Use `python3 -u` (unbuffered) so logs appear in `docker logs` immediately.

## Installing syft / grype in a Dockerfile

```dockerfile
# Install syft (SBOM generator)
RUN curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
    | sh -s -- -b /usr/local/bin

# Install grype (CVE matcher)
RUN curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \
    | sh -s -- -b /usr/local/bin
```

Both install the latest stable version. For reproducible builds, pin the version:
```dockerfile
RUN curl -sSfL .../install.sh | sh -s -- -b /usr/local/bin v1.16.0
```

## Docker socket access

Services that need to scan or manage Docker images need the socket mounted:
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro   # read-only for scanner
  - /var/run/docker.sock:/var/run/docker.sock      # read-write for executor
```

Scout: mount read-only. Hub (DockerAdapter): mount read-write.

## Volume for grype vulnerability database

grype stores its DB at `$GRYPE_DB_CACHE_DIR`. Set it to a named volume so it persists across hub restarts:
```yaml
environment:
  - GRYPE_DB_CACHE_DIR=/data/grype-db
volumes:
  - beaconry-data:/data
```

First startup runs `grype db update` (~200MB download). Subsequent starts use cached DB.

## Port binding

Hub is internal only in production (scout reaches it via Docker network `beaconry-hub:8000`).
Expose `8000` on the host only for local development:
```yaml
ports:
  - "127.0.0.1:8000:8000"  # localhost only
```
