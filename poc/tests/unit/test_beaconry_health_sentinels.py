"""BC-012 — SentinelWriter in beaconry_health.sentinels.

Scout's file-sentinel plumbing moves out of poc/scout/health.py into
the shared package. BC-010 scout tests must still pass unchanged after
the migration.

Covers:
    - CAP-T040: Shared `beaconry_health/` package provides sentinel writer
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from poc.beaconry_health.sentinels import SentinelWriter


def test_sentinel_writer_live_updates_mtime(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.live()
    f = tmp_path / "scout.live"
    assert f.exists()
    first = f.stat().st_mtime
    time.sleep(0.05)
    w.live()
    assert f.stat().st_mtime >= first


def test_sentinel_writer_ready_is_one_shot(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.ready()
    f = tmp_path / "scout.ready"
    first = f.stat().st_mtime
    time.sleep(0.05)
    w.ready()
    assert f.stat().st_mtime == first


def test_sentinel_writer_started_is_one_shot(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.started()
    f = tmp_path / "scout.started"
    first = f.stat().st_mtime
    time.sleep(0.05)
    w.started()
    assert f.stat().st_mtime == first


def test_sentinel_writer_healthz_round_trip(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.healthz(status="ok", last_scan_ts=1_700_000_000, images_seen=11, bytes_posted=4321)
    body = json.loads((tmp_path / "scout.healthz.json").read_text())
    assert body == {
        "status": "ok",
        "last_scan_ts": 1_700_000_000,
        "images_seen": 11,
        "bytes_posted": 4321,
    }


def test_sentinel_writer_creates_dir_if_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    w = SentinelWriter(missing)
    w.live()
    assert (missing / "scout.live").exists()


def test_sentinel_writer_healthz_overwrites(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.healthz(status="ok", last_scan_ts=1, images_seen=1, bytes_posted=1)
    w.healthz(status="degraded", last_scan_ts=2, images_seen=0, bytes_posted=0)
    body = json.loads((tmp_path / "scout.healthz.json").read_text())
    assert body["status"] == "degraded"
    assert body["images_seen"] == 0
