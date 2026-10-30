"""BC-010 — scout file-sentinel health indicators.

Covers:
    - CAP-T036: Scout implements the four-endpoint health convention via file-sentinels or HTTP
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from poc.scout import health as scout_health


def test_write_live_updates_mtime(tmp_path: Path) -> None:
    f = tmp_path / "scout.live"
    scout_health.write_live(tmp_path)
    assert f.exists()
    mtime_1 = f.stat().st_mtime
    time.sleep(0.05)
    scout_health.write_live(tmp_path)
    assert f.stat().st_mtime >= mtime_1


def test_write_ready_is_one_shot(tmp_path: Path) -> None:
    f = tmp_path / "scout.ready"
    assert not f.exists()
    scout_health.write_ready(tmp_path)
    assert f.exists()
    first_mtime = f.stat().st_mtime
    time.sleep(0.05)
    scout_health.write_ready(tmp_path)  # idempotent, must not raise
    assert f.stat().st_mtime == first_mtime, "ready is one-shot; mtime must not bump"


def test_write_started_is_one_shot(tmp_path: Path) -> None:
    f = tmp_path / "scout.started"
    scout_health.write_started(tmp_path)
    assert f.exists()
    first_mtime = f.stat().st_mtime
    time.sleep(0.05)
    scout_health.write_started(tmp_path)
    assert f.stat().st_mtime == first_mtime


def test_write_healthz_json_schema(tmp_path: Path) -> None:
    scout_health.write_healthz(
        tmp_path,
        status="ok",
        last_scan_ts=1_700_000_000,
        images_seen=11,
        bytes_posted=4321,
    )
    f = tmp_path / "scout.healthz.json"
    assert f.exists()
    body = json.loads(f.read_text())
    assert body["status"] == "ok"
    assert body["last_scan_ts"] == 1_700_000_000
    assert body["images_seen"] == 11
    assert body["bytes_posted"] == 4321


def test_write_healthz_overwrites(tmp_path: Path) -> None:
    scout_health.write_healthz(tmp_path, status="ok", last_scan_ts=1, images_seen=1, bytes_posted=1)
    scout_health.write_healthz(
        tmp_path, status="degraded", last_scan_ts=2, images_seen=0, bytes_posted=0
    )
    body = json.loads((tmp_path / "scout.healthz.json").read_text())
    assert body["status"] == "degraded"
    assert body["images_seen"] == 0


def test_write_live_creates_directory_if_missing(tmp_path: Path) -> None:
    missing = tmp_path / "doesnt_exist_yet"
    scout_health.write_live(missing)
    assert (missing / "scout.live").exists()
