"""File-sentinel writer — for services without an HTTP server (e.g. scout).

Four indicators per `memory/health-checks.md § Scout analogue`:

  scout.live          — bumped every loop iteration (mtime == liveness)
  scout.ready         — one-shot: local-prereq probe succeeded
  scout.started       — one-shot: first outbound work completed
  scout.healthz.json  — overwritten each loop; the summary a reader consumes

Compose healthchecks read `scout.live` + `scout.ready`; `make status` reads
`scout.healthz.json`. BC-013 will replace this entire path with HTTP endpoints
on scout, at which point this module can go away for new services — but
remains available for pure-loop services that intentionally stay HTTP-less.
"""

from __future__ import annotations

import json
from pathlib import Path

LIVE = "scout.live"
READY = "scout.ready"
STARTED = "scout.started"
HEALTHZ = "scout.healthz.json"


class SentinelWriter:
    """Writes the four indicator files under a shared data dir.

    Instantiate once per loop; call `.live()` at the top of every iteration,
    `.ready()` once after local prereqs are satisfied, `.started()` once after
    the first successful outbound work unit, and `.healthz(...)` at the end of
    every iteration with the current summary.
    """

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def live(self) -> None:
        """Bump scout.live mtime. Read by compose healthcheck + make status."""
        self._ensure_dir()
        (self._dir / LIVE).touch()

    def ready(self) -> None:
        """Create scout.ready if not already present. Idempotent on repeat calls."""
        self._ensure_dir()
        f = self._dir / READY
        if not f.exists():
            f.touch()

    def started(self) -> None:
        """Create scout.started if not already present. Idempotent on repeat calls.

        Reserved for BC-013. Written now to keep the four-indicator convention
        complete; production reader (scout `/startupz` endpoint) lands when
        scout grows a live HTTP server. `last_scan_ts > 0` in `.healthz` output
        carries the same signal in the interim.
        """
        self._ensure_dir()
        f = self._dir / STARTED
        if not f.exists():
            f.touch()

    def healthz(
        self,
        *,
        status: str,
        last_scan_ts: int,
        images_seen: int,
        bytes_posted: int,
    ) -> None:
        """Overwrite scout.healthz.json with the current loop summary."""
        self._ensure_dir()
        body = {
            "status": status,
            "last_scan_ts": last_scan_ts,
            "images_seen": images_seen,
            "bytes_posted": bytes_posted,
        }
        (self._dir / HEALTHZ).write_text(json.dumps(body))
