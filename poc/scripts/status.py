"""BC-010 `make status` — one-line operator view of beaconry service health.

Reads hub /healthz over localhost and scout.healthz.json from the shared /data
volume. Prints `hub: healthy ... · scout: healthy ...` or the degraded equivalent
with the specific failing check named. Exit 0 all-healthy, non-zero otherwise.

Sibling to `poc/scripts/findings.py` (BC-007). Covers CAP-B015 + CAP-T039.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HUB_HEALTHZ_URL = os.environ.get("BEACONRY_HUB_HEALTHZ_URL", "http://localhost:8000/healthz")
SCOUT_DATA_DIR = Path(os.environ.get("BEACONRY_SCOUT_DATA_DIR", "/data"))
SCOUT_LIVE_MAX_AGE_S = int(os.environ.get("BEACONRY_SCOUT_LIVE_MAX_AGE_S", "300"))
SCOUT_CONTAINER = os.environ.get("BEACONRY_SCOUT_CONTAINER", "beaconry-scout")


def fetch_hub_healthz(url: str = HUB_HEALTHZ_URL, timeout: float = 2.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — localhost
        return json.loads(resp.read().decode())


def _format_hub_line(body: dict) -> str:
    checks = body.get("checks", {})
    status = body.get("status", "unknown")
    last_scan_ts = checks.get("last_scan_ts", 0)
    last_scan_s = int(time.time()) - int(last_scan_ts) if last_scan_ts else -1
    failed = [
        k for k, v in checks.items() if k != "last_scan_ts" and v not in ("ok", "polling", "mock")
    ]
    tail = f", last scan {last_scan_s}s ago" if last_scan_ts else ", no scans yet"
    if status == "ok":
        return f"hub: healthy (grype_db ok, telegram {checks.get('telegram')}{tail})"
    return f"hub: degraded (failing: {', '.join(failed) or 'unknown'}{tail})"


def _fetch_scout_body_via_docker(container: str) -> tuple[dict, int] | None:
    """Read scout.healthz.json + scout.live mtime via `docker exec`. None if container missing."""
    try:
        healthz_proc = subprocess.run(
            ["docker", "exec", container, "cat", "/data/scout.healthz.json"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if healthz_proc.returncode != 0:
            return None
        live_proc = subprocess.run(
            ["docker", "exec", container, "stat", "-c", "%Y", "/data/scout.live"],
            capture_output=True,
            timeout=5,
            check=False,
            text=True,
        )
        live_mtime = int(live_proc.stdout.strip()) if live_proc.returncode == 0 else 0
        return json.loads(healthz_proc.stdout.decode()), live_mtime
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, ValueError):
        return None


def _format_scout_line(data_dir: Path) -> tuple[str, bool]:
    body: dict
    live_mtime = 0

    # Prefer docker exec (works regardless of volume mount permissions).
    docker_result = _fetch_scout_body_via_docker(SCOUT_CONTAINER)
    if docker_result is not None:
        body, live_mtime = docker_result
    else:
        # Fallback to filesystem path (local dev or custom data dir).
        healthz = data_dir / "scout.healthz.json"
        live = data_dir / "scout.live"
        if not healthz.exists():
            return "scout: unreachable (no scout.healthz.json; container not up?)", False
        try:
            body = json.loads(healthz.read_text())
            live_mtime = int(live.stat().st_mtime) if live.exists() else 0
        except (OSError, json.JSONDecodeError) as e:
            return f"scout: unreachable (healthz read error: {e})", False

    status = body.get("status", "unknown")
    last_scan_ts = int(body.get("last_scan_ts", 0))
    now = int(time.time())
    last_scan_s = now - last_scan_ts if last_scan_ts else -1
    images = body.get("images_seen", 0)

    live_ok = (now - live_mtime) <= SCOUT_LIVE_MAX_AGE_S if live_mtime else True
    if status == "ok" and live_ok:
        tail = f"last scan {last_scan_s}s ago, {images} images" if last_scan_ts else "no scans yet"
        return f"scout: healthy ({tail})", True
    return f"scout: degraded (status={status}, live_ok={live_ok})", False


def main() -> int:
    try:
        hub_body = fetch_hub_healthz()
        hub_line = _format_hub_line(hub_body)
        hub_ok = hub_body.get("status") == "ok"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        hub_line = f"hub: unreachable ({type(e).__name__})"
        hub_ok = False

    scout_line, scout_ok = _format_scout_line(SCOUT_DATA_DIR)
    print(f"{hub_line} · {scout_line}")
    return 0 if (hub_ok and scout_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
