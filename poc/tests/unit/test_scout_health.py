"""BC-010 scout sentinels — behavior-preservation harness (BC-012 migrated the writers to beaconry_health).

Scout's four file-sentinel indicators still behave exactly as BC-010 shipped
them. The implementation moved to `poc/beaconry_health/sentinels.py` in
BC-012; these tests continue to assert the scout-side contract via a thin
import so Shipping evidence pointers from BC-010 (CAP-T036) stay valid.

Covers:
    - CAP-T036: Scout implements the four-endpoint health convention via file-sentinels or HTTP
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from poc.beaconry_health import SentinelWriter


def test_write_live_updates_mtime(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.live()
    f = tmp_path / "scout.live"
    assert f.exists()
    mtime_1 = f.stat().st_mtime
    time.sleep(0.05)
    w.live()
    assert f.stat().st_mtime >= mtime_1


def test_write_ready_is_one_shot(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.ready()
    f = tmp_path / "scout.ready"
    first_mtime = f.stat().st_mtime
    time.sleep(0.05)
    w.ready()
    assert f.stat().st_mtime == first_mtime


def test_write_started_is_one_shot(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.started()
    f = tmp_path / "scout.started"
    first_mtime = f.stat().st_mtime
    time.sleep(0.05)
    w.started()
    assert f.stat().st_mtime == first_mtime


def test_write_healthz_json_schema(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.healthz(status="ok", last_scan_ts=1_700_000_000, images_seen=11, bytes_posted=4321)
    body = json.loads((tmp_path / "scout.healthz.json").read_text())
    assert body == {
        "status": "ok",
        "last_scan_ts": 1_700_000_000,
        "images_seen": 11,
        "bytes_posted": 4321,
    }


def test_write_healthz_overwrites(tmp_path: Path) -> None:
    w = SentinelWriter(tmp_path)
    w.healthz(status="ok", last_scan_ts=1, images_seen=1, bytes_posted=1)
    w.healthz(status="degraded", last_scan_ts=2, images_seen=0, bytes_posted=0)
    body = json.loads((tmp_path / "scout.healthz.json").read_text())
    assert body["status"] == "degraded"
    assert body["images_seen"] == 0


def test_write_live_creates_directory_if_missing(tmp_path: Path) -> None:
    missing = tmp_path / "doesnt_exist_yet"
    w = SentinelWriter(missing)
    w.live()
    assert (missing / "scout.live").exists()
