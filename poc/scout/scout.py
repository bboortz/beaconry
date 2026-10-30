"""
Beaconry Scout 0.1.0

Read-only inventory collector. Generates SBOMs via syft, posts to hub.
Never executes anything. No CVE matching — that is the hub's job.

Scans:
  - Docker images on the host (via Docker socket + syft)
  - Arch Linux packages (via pacman local DB parser)
  - Open TCP ports (via ss -tlnp)
"""

import fnmatch
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import docker
import docker.errors
import requests

try:
    # Container path: beaconry_health COPY'd next to scout.py on /app
    from beaconry_health import SentinelWriter
except ImportError:
    # Host test path: imported as poc.scout.scout
    from poc.beaconry_health import SentinelWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scout] %(levelname)s %(message)s",
)
log = logging.getLogger("scout")

HUB_URL = os.environ.get("HUB_URL", "http://hub:8000")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
HOST_NAME = os.environ.get("HOST_NAME", os.uname().nodename)
PACMAN_DB = os.environ.get("PACMAN_DB", "/host/var/lib/pacman")
DATA_DIR = Path(os.environ.get("SCOUT_DATA_DIR", "/data"))
_sentinels = SentinelWriter(DATA_DIR)

# BC-005 defaults — demo-safe allowlist, no excludes by default.
DEFAULT_INCLUDE_GLOBS = "nginx*,redis*,postgres*"


def parse_globs(env: str) -> list[str]:
    """Parse a comma-separated glob list from an env var. Whitespace tolerated. Empty → []."""
    return [piece.strip() for piece in env.split(",") if piece.strip()]


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


def syft_scan(target: str) -> dict | None:
    result = subprocess.run(
        ["syft", target, "-o", "syft-json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=120,
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


def scan_docker_images() -> list[dict]:
    try:
        client = docker.from_env()
    except docker.errors.DockerException as e:
        log.warning("Docker socket unavailable: %s", e)
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


def scan_pacman(db_path: str) -> dict | None:
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


def scan_open_ports() -> list[dict]:
    result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True)
    ports = []
    for line in result.stdout.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        addr = parts[3]
        port = addr.rsplit(":", 1)[-1]
        if port.isdigit():
            ports.append({"port": port, "address": addr, "raw": line})
    return ports


# ── Main loop ─────────────────────────────────────────────────────────────────


def collect_and_post(once: bool = False):
    log.info("Scan starting — host: %s", HOST_NAME)
    _sentinels.live()
    sboms: list[dict] = []
    images_seen = 0
    bytes_posted = 0

    try:
        docker_sboms = scan_docker_images()
        sboms.extend(docker_sboms)
        images_seen = len(docker_sboms)
        log.info("Docker: %d image(s) scanned", len(docker_sboms))
        if docker_sboms:
            _sentinels.ready()
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

    if not sboms:
        log.warning("No SBOMs collected — nothing to send")
        _sentinels.healthz(
            status="degraded",
            last_scan_ts=int(time.time()),
            images_seen=0,
            bytes_posted=0,
        )
        return

    payload = {
        "host": HOST_NAME,
        "timestamp": int(time.time()),
        "sboms": sboms,
        "open_ports": open_ports,
    }
    post_ok = False
    try:
        body = json.dumps(payload)
        bytes_posted = len(body)
        resp = requests.post(
            f"{HUB_URL}/scan", data=body, headers={"Content-Type": "application/json"}, timeout=120
        )
        resp.raise_for_status()
        data = resp.json()
        log.info("Hub response: %d new finding(s)", data.get("findings", 0))
        post_ok = True
        _sentinels.started()
    except requests.RequestException as e:
        log.error("Failed to post to hub: %s", e)

    _sentinels.healthz(
        status="ok" if post_ok else "degraded",
        last_scan_ts=int(time.time()),
        images_seen=images_seen,
        bytes_posted=bytes_posted,
    )


def main():
    once = "--once" in sys.argv
    log.info("Beaconry Scout 0.1.0 — hub: %s, interval: %ds", HUB_URL, SCAN_INTERVAL)

    if once:
        collect_and_post(once=True)
        return

    while True:
        try:
            collect_and_post()
        except Exception as e:
            log.error("Scan cycle error: %s", e)
        log.info("Next scan in %ds", SCAN_INTERVAL)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
