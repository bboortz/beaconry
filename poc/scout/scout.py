"""
Beaconry Scout 0.1.0

Read-only inventory collector. Generates SBOMs via syft, posts to hub.
Never executes anything. No CVE matching — that is the hub's job.

Scans:
  - Docker images on the host (via Docker socket + syft)
  - Arch Linux packages (via pacman local DB parser)
  - Open TCP ports (via ss -tlnp)

BC-013: scout runs a tiny FastAPI HTTP server on port 8001 serving the
four endpoints via `beaconry_health.build_router`. The scan loop runs as
a background asyncio task via `asyncio.to_thread(collect_and_post)` so
the HTTP event loop stays responsive during long-running scans. SentinelWriter
(BC-010) is no longer used — kept available in `beaconry_health` for
future HTTP-less loop services.
"""

import asyncio
import fnmatch
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import docker
import docker.errors
import requests
import uvicorn
from fastapi import FastAPI

try:
    # Container path: beaconry_health COPY'd next to scout.py on /app
    from beaconry_health import build_router
except ImportError:
    # Host test path: imported as poc.scout.scout
    from poc.beaconry_health import build_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scout] %(levelname)s %(message)s",
)
log = logging.getLogger("scout")

# Suppress noisy loggers (httpx might leak scan payloads at INFO).
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

HUB_URL = os.environ.get("HUB_URL", "http://hub:8000")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
HOST_NAME = os.environ.get("HOST_NAME", os.uname().nodename)
PACMAN_DB = os.environ.get("PACMAN_DB", "/host/var/lib/pacman")
HTTP_PORT = int(os.environ.get("SCOUT_HTTP_PORT", "8001"))

# BC-005 defaults — demo-safe allowlist, no excludes by default.
DEFAULT_INCLUDE_GLOBS = "nginx*,redis*,postgres*"

# BC-013 health state — mutable module globals read by /readyz /healthz providers.
_docker_socket_ok: bool = False
_started: bool = False
_last_scan_ts: int = 0
_images_seen: int = 0
_bytes_posted: int = 0
SCOUT_VERSION = "0.1.0"

# BC-037: on-demand scan trigger. Single-threaded event loop → a bool is a
# sufficient mutex. Loop + manual trigger share this guard so a concurrent
# trigger short-circuits with 409 instead of double-scanning the inventory.
_scan_in_progress: bool = False


def parse_globs(env: str) -> list[str]:
    """Parse a comma-separated glob list from an env var. Whitespace tolerated. Empty → []."""
    return [piece.strip() for piece in env.split(",") if piece.strip()]


# ── BC-030 — container port publication → internet_facing map ────────────────

# Loopback-only addresses that must NOT count as internet-facing. All other
# non-empty values (including "0.0.0.0", "::", and any routable host IP) do.
_LOOPBACK_HOST_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def is_container_internet_facing(ports: dict | None) -> bool:
    """Return True if `ports` (Docker's NetworkSettings.Ports shape) has at
    least one binding to a non-loopback host IP.

    Shape: ``{"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}], ...}``.
    An unpublished port carries `None` or missing bindings → not exposed.
    Empty HostIp (Docker's default binding) counts as exposed — equivalent to
    0.0.0.0 for most daemons.
    """
    if not ports:
        return False
    for bindings in ports.values():
        if not bindings:
            continue
        for b in bindings:
            host_ip = (b.get("HostIp") or "").strip()
            if host_ip and host_ip in _LOOPBACK_HOST_IPS:
                continue
            # Empty HostIp OR non-loopback IP → routable from outside the host.
            return True
    return False


def collect_container_ports() -> dict[str, bool]:  # pragma: no cover - docker.from_env wrapper
    """Return `{image_tag: is_internet_facing}` for every running container.

    If multiple containers share an image and any publishes externally, the
    image is marked True. Images with no running container simply don't
    appear — hub treats absence as `False`.
    """
    try:
        client = docker.from_env()
    except docker.errors.DockerException as e:
        log.warning("Docker socket unavailable for port collection: %s", e)
        return {}

    out: dict[str, bool] = {}
    try:
        containers = client.containers.list()
    except docker.errors.DockerException as e:
        log.warning("Container list failed: %s", e)
        return {}
    for container in containers:
        ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        exposed = is_container_internet_facing(ports)
        image = container.image
        if image is None:
            continue
        for tag in image.tags:
            out[tag] = out.get(tag, False) or exposed
    return out


def filter_image_tags(
    tags: list[str],
    *,
    include_globs: list[str],
    exclude_globs: list[str],
) -> list[str]:
    """Return tags matching any include glob AND no exclude glob.

    If include_globs is empty, nothing passes. If exclude_globs is empty,
    only include is applied. Exclude always overrides include.
    """
    return [
        tag
        for tag in tags
        if any(fnmatch.fnmatchcase(tag, g) for g in include_globs)
        and not any(fnmatch.fnmatchcase(tag, g) for g in exclude_globs)
    ]


# ── syft ─────────────────────────────────────────────────────────────────────


def syft_scan(
    target: str,
) -> dict | None:  # pragma: no cover - subprocess wrapper around syft binary
    result = subprocess.run(
        ["syft", target, "-o", "syft-json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        log.warning("syft failed for %s: %s", target, result.stderr[:200])
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log.warning("syft output parse error for %s: %s", target, e)
        return None


# ── Docker images ─────────────────────────────────────────────────────────────


def scan_docker_images() -> list[
    dict
]:  # pragma: no cover - docker.from_env() + subprocess around syft
    global _docker_socket_ok
    try:
        client = docker.from_env()
        _docker_socket_ok = True
    except docker.errors.DockerException as e:
        log.warning("Docker socket unavailable: %s", e)
        _docker_socket_ok = False
        return []

    include_globs = parse_globs(os.environ.get("SCOUT_INCLUDE", DEFAULT_INCLUDE_GLOBS))
    exclude_globs = parse_globs(os.environ.get("SCOUT_EXCLUDE", ""))

    all_tags: list[str] = []
    for image in client.images.list():
        all_tags.extend(t for t in image.tags if "<none>" not in t)
    matching = filter_image_tags(all_tags, include_globs=include_globs, exclude_globs=exclude_globs)
    skipped = sorted(set(all_tags) - set(matching))
    log.info(
        "Image inventory: %d total, %d matched (%s), %d skipped",
        len(all_tags),
        len(matching),
        ", ".join(include_globs) or "<none>",
        len(skipped),
    )
    if skipped:
        log.info("Skipped tags: %s", ", ".join(skipped[:10]) + (" …" if len(skipped) > 10 else ""))
    if not matching and all_tags:
        log.warning(
            "0 image(s) matched SCOUT_INCLUDE=%r — nothing to scan. "
            'Adjust the glob (e.g. SCOUT_INCLUDE="*" to scan everything).',
            os.environ.get("SCOUT_INCLUDE", DEFAULT_INCLUDE_GLOBS),
        )

    results = []
    for tag in matching:
        log.info("Scanning image: %s", tag)
        sbom = syft_scan(tag)
        if sbom:
            results.append(
                {
                    "source_meta": {"type": "docker", "image": tag},
                    "sbom": sbom,
                }
            )
    return results


# ── Arch Linux packages ───────────────────────────────────────────────────────


def parse_pacman_desc(text: str) -> dict:
    fields: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("%") and line.endswith("%"):
            key = line.strip("%")
        elif key and line:
            fields[key] = line
            key = None
    return fields


def scan_pacman(
    db_path: str,
) -> dict | None:  # pragma: no cover - filesystem walk; parse_pacman_desc is unit-tested
    local = Path(db_path) / "local"
    if not local.exists():
        log.warning("Pacman DB not found at %s", local)
        return None

    artifacts = []
    for pkg_dir in local.iterdir():
        desc_file = pkg_dir / "desc"
        if not desc_file.exists():
            continue
        try:
            fields = parse_pacman_desc(desc_file.read_text(errors="replace"))
        except OSError:
            continue

        name = fields.get("NAME", "")
        version = fields.get("VERSION", "")
        if not name or not version:
            continue

        artifacts.append(
            {
                "id": f"alpm-{name}",
                "name": name,
                "version": version,
                "type": "alpm",
                "language": "",
                "cpes": [],
                "purl": f"pkg:alpm/arch/{name}@{version}",
                "metadataType": "AlpmPackage",
                "metadata": {"basePackage": name},
            }
        )

    if not artifacts:
        return None

    log.info("Arch packages: %d found in pacman DB", len(artifacts))
    return {
        "schema": {
            "version": "16.0.10",
            "url": "https://raw.githubusercontent.com/anchore/syft/main/schema/json/schema-16.0.10.json",
        },
        "artifacts": artifacts,
        "artifactRelationships": [],
        "source": {"type": "directory", "target": "/"},
        "distro": {"name": "Arch Linux", "id": "arch", "versionID": "rolling"},
        "descriptor": {"name": "beaconry-scout", "version": "0.1.0"},
    }


# ── Open ports ────────────────────────────────────────────────────────────────


def parse_ss_output(stdout: str) -> list[dict]:
    """Parse `ss -tlnp` output into a list of {port, address, raw} dicts.

    Pure function — extracted from scan_open_ports so the parsing logic is
    unit-testable without an actual `ss` binary. First line is the header.
    """
    ports: list[dict] = []
    for line in stdout.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        addr = parts[3]
        port = addr.rsplit(":", 1)[-1]
        if port.isdigit():
            ports.append({"port": port, "address": addr, "raw": line})
    return ports


def scan_open_ports() -> list[
    dict
]:  # pragma: no cover - subprocess wrapper; parse_ss_output is unit-tested
    result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True)
    return parse_ss_output(result.stdout)


# ── Scan loop ─────────────────────────────────────────────────────────────────


def collect_and_post():  # pragma: no cover - orchestrator; integration-tested live via test-e2e
    """One scan iteration. Bumps module-local state so /healthz surfaces metrics."""
    global _last_scan_ts, _images_seen, _bytes_posted, _started

    log.info("Scan starting — host: %s", HOST_NAME)
    sboms: list[dict] = []
    images_seen = 0
    bytes_posted = 0

    try:
        docker_sboms = scan_docker_images()
        sboms.extend(docker_sboms)
        images_seen = len(docker_sboms)
        log.info("Docker: %d image(s) scanned", len(docker_sboms))
    except Exception as e:
        log.error("Docker scan error: %s", e)

    try:
        pacman_sbom = scan_pacman(PACMAN_DB)
        if pacman_sbom:
            sboms.append(
                {
                    "source_meta": {"type": "alpm", "host": HOST_NAME},
                    "sbom": pacman_sbom,
                }
            )
    except Exception as e:
        log.error("Pacman scan error: %s", e)

    open_ports: list[dict] = []
    try:
        open_ports = scan_open_ports()
    except Exception as e:
        log.error("Port scan error: %s", e)

    # BC-030: {image_tag: is_internet_facing} map from running containers.
    container_exposure: dict[str, bool] = {}
    try:
        container_exposure = collect_container_ports()
        if container_exposure:
            exposed = [k for k, v in container_exposure.items() if v]
            log.info(
                "Container exposure: %d image(s) tracked, %d internet-facing",
                len(container_exposure),
                len(exposed),
            )
    except Exception as e:
        log.error("Container port collection error: %s", e)

    _last_scan_ts = int(time.time())
    _images_seen = images_seen

    if not sboms:
        log.warning("No SBOMs collected — nothing to send")
        return

    payload = {
        "host": HOST_NAME,
        "timestamp": int(time.time()),
        "sboms": sboms,
        "open_ports": open_ports,
        "container_exposure": container_exposure,
    }
    try:
        body = json.dumps(payload)
        bytes_posted = len(body)
        _bytes_posted = bytes_posted
        # BC-055: attach Bearer when HUB_TOKEN is set; empty = no header
        # (back-compat for single-host operators who don't need auth).
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("HUB_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.post(f"{HUB_URL}/scan", data=body, headers=headers, timeout=1800)
        resp.raise_for_status()
        data = resp.json()
        log.info("Hub response: %d new finding(s)", data.get("findings", 0))
        _started = True
    except requests.RequestException as e:
        log.error("Failed to post to hub: %s", e)


# ── Health providers for build_router ─────────────────────────────────────────


def _scout_readyz_providers() -> dict[str, str]:
    """Local prereqs — Docker socket is the only external dep scout needs."""
    return {"docker_socket": "ok" if _docker_socket_ok else "unavailable"}


def _scout_healthz_summary() -> dict:
    checks = {
        "docker_socket": "ok" if _docker_socket_ok else "unavailable",
        "last_scan_ts": _last_scan_ts,
        "images_seen": _images_seen,
        "bytes_posted": _bytes_posted,
    }
    degraded = not _docker_socket_ok
    return {
        "status": "degraded" if degraded else "ok",
        "version": SCOUT_VERSION,
        "checks": checks,
    }


# ── FastAPI app + background scan task ───────────────────────────────────────


def _try_acquire_scan_lock() -> bool:
    """BC-037: synchronous flag flip, usable by the HTTP handler so the
    `_scan_in_progress=True` state is visible to the very next request even
    before the background task runs. Returns True if we got the lock."""
    global _scan_in_progress
    if _scan_in_progress:
        return False
    _scan_in_progress = True
    return True


def _release_scan_lock() -> None:
    global _scan_in_progress
    _scan_in_progress = False


async def _run_scan_once(reason: str) -> str:
    """BC-037: single guarded entry point for both the scheduled loop and the
    manual trigger. Returns one of:
      - "ran"            — scan executed, flag released
      - "already_running" — another scan was in progress; this call no-op'd
    `reason` is only used in logs — distinguishes "loop" vs "manual" ingest."""
    if not _try_acquire_scan_lock():
        log.info("Scan skipped (%s): another scan already in progress", reason)
        return "already_running"
    log.info("Scan triggered: %s", reason)
    try:
        await asyncio.to_thread(collect_and_post)
    except Exception as e:
        log.error("Scan cycle error (%s): %s", reason, e)
    finally:
        _release_scan_lock()
    return "ran"


async def _run_already_held(reason: str) -> None:
    """BC-037: run `collect_and_post` with the lock ALREADY held by the caller.
    Used by the HTTP trigger — the handler acquires sync, the background task
    just does the work and releases. Must not re-check the lock."""
    log.info("Scan triggered: %s", reason)
    try:
        await asyncio.to_thread(collect_and_post)
    except Exception as e:
        log.error("Scan cycle error (%s): %s", reason, e)
    finally:
        _release_scan_lock()


async def _scan_loop_forever() -> None:  # pragma: no cover - infinite async loop; tested live
    """Background task: run `_run_scan_once` every SCAN_INTERVAL seconds.

    The sleep is independent of scan duration — a long scan does not postpone
    the next tick, and a BC-037 manual trigger does not shift this timer
    (they share the mutex, not the schedule)."""
    while True:
        await _run_scan_once(reason="loop")
        log.info("Next scan in %ds", SCAN_INTERVAL)
        await asyncio.sleep(SCAN_INTERVAL)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("Beaconry Scout %s — hub: %s, interval: %ds", SCOUT_VERSION, HUB_URL, SCAN_INTERVAL)
    task = asyncio.create_task(_scan_loop_forever())
    yield
    task.cancel()


app = FastAPI(title="Beaconry Scout", version=SCOUT_VERSION, lifespan=lifespan)
app.include_router(
    build_router(
        livez_provider=lambda: "alive",
        readyz_providers=_scout_readyz_providers,
        startup_flag_getter=lambda: _started,
        healthz_summary_getter=_scout_healthz_summary,
    )
)


@app.post("/scan/trigger")
async def trigger_scan() -> "_FastAPIResponseOrDict":
    """BC-037: operator-triggered scan cycle. Returns 202 Accepted with a
    background task running `_run_scan_once`; concurrent trigger → 409."""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    now_ts = int(time.time())
    if not _docker_socket_ok:
        raise HTTPException(
            status_code=503,
            detail="Docker socket unavailable — cannot run scan. Check scout logs.",
        )
    # Synchronous lock-acquire: flip the flag HERE so the very next request
    # — even before our background task starts — sees `_scan_in_progress=True`
    # and gets 409. Deferring the flip to inside the task would race.
    if not _try_acquire_scan_lock():
        return JSONResponse(
            status_code=409,
            content={"status": "already_running", "started_at": now_ts},
        )
    # Fire-and-forget — handler returns immediately, scan runs in the background.
    # The background task releases the lock in its finally.
    asyncio.create_task(_run_already_held(reason="manual"))
    return JSONResponse(
        status_code=202,
        content={"status": "scheduled", "started_at": now_ts},
    )


# Pure type alias; keeps the return annotation parseable without pulling in
# fastapi.Response at module import time.
_FastAPIResponseOrDict = object  # noqa: N816


def main():  # pragma: no cover - uvicorn + asyncio entry; tested live
    once = "--once" in sys.argv
    if once:
        collect_and_post()
        return
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
