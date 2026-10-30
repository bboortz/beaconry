"""BC-010 scout file-sentinels — the four-indicator convention via /data/.

Scout is a pure loop (no HTTP server). Compose healthcheck + make status read
these sentinels per `memory/health-checks.md § Scout analogue`.

  /data/scout.live          — bumped every loop iteration (mtime == liveness)
  /data/scout.ready         — one-shot: Docker socket probe succeeded
  /data/scout.started       — one-shot: first /scan POST returned 2xx
  /data/scout.healthz.json  — written each loop, read by `make status`
"""

from __future__ import annotations

import json
from pathlib import Path

LIVE = "scout.live"
READY = "scout.ready"
STARTED = "scout.started"
HEALTHZ = "scout.healthz.json"


def _ensure_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)


def write_live(data_dir: Path) -> None:
    """Touch scout.live every loop iteration. Mtime == liveness."""
    _ensure_dir(data_dir)
    (data_dir / LIVE).touch()


def write_ready(data_dir: Path) -> None:
    """One-shot: Docker socket probe succeeded. Idempotent on repeat calls."""
    _ensure_dir(data_dir)
    f = data_dir / READY
    if not f.exists():
        f.touch()


def write_started(data_dir: Path) -> None:
    """One-shot: first /scan POST returned 2xx. Idempotent on repeat calls.

    Reserved for BC-013. Written now to keep the four-indicator convention
    complete; the production reader (scout `/startupz` endpoint) lands when
    scout grows a live HTTP server. `scout.healthz.json`'s `last_scan_ts > 0`
    carries the same signal in the interim.
    """
    _ensure_dir(data_dir)
    f = data_dir / STARTED
    if not f.exists():
        f.touch()


def write_healthz(
    data_dir: Path,
    *,
    status: str,
    last_scan_ts: int,
    images_seen: int,
    bytes_posted: int,
) -> None:
    """Overwrite scout.healthz.json with the current loop's summary."""
    _ensure_dir(data_dir)
    body = {
        "status": status,
        "last_scan_ts": last_scan_ts,
        "images_seen": images_seen,
        "bytes_posted": bytes_posted,
    }
    (data_dir / HEALTHZ).write_text(json.dumps(body))
